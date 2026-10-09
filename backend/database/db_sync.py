"""Transactional SnapTrade snapshot importer shared by the API and CLI."""
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from flask import has_app_context
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.models import db, Account, Position, Order, Activity

EXPORT_FILE = os.path.join(os.environ.get("ACCOUNT_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data-export")), "snaptrade_full_export.json")


# ── Helpers ───────────────────────────────
def parse_dt(val):
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None

def safe_dict(x):
    return x if isinstance(x, dict) else {}

def safe_currency(obj):
    return obj.get("code") if isinstance(obj, dict) else None

def is_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, TypeError, AttributeError):
        return False

def extract_symbol(obj):
    if not obj:
        return None
    if isinstance(obj, str):
        return None if is_uuid(obj) else obj
    if isinstance(obj, dict):
        for key in ["symbol", "raw_symbol", "ticker"]:
            val = obj.get(key)
            if isinstance(val, str) and not is_uuid(val):
                return val
        inner = obj.get("symbol")
        if isinstance(inner, dict):
            val = inner.get("symbol") or inner.get("raw_symbol")
            if val and not is_uuid(val):
                return val
    return None

def sync_export_to_db(export_file=None, db_url=None, verbose=False):
    """Import account snapshots atomically into the application's database.

    Accounts absent from an export are preserved. Invalid snapshots roll back.
    An explicit db_url is supported for maintenance and isolated tests.
    """
    export_file = export_file or EXPORT_FILE
    with open(export_file) as f:
        data = json.load(f)
    if not isinstance(data, dict) or not ({"users", "accounts"} & data.keys()):
        raise ValueError("Export must contain users or accounts")
    wrappers = []
    users = data.get("users", [])
    if "accounts" in data:
        users = [*users, {"username": data.get("username", "default_user"), "accounts": data["accounts"]}]
    seen = set()
    for user in users:
        username = user.get("username")
        if not username or not isinstance(user.get("accounts"), list):
            raise ValueError("Each exported user requires a username and accounts list")
        for wrapper in user["accounts"]:
            account_id = safe_dict(wrapper.get("account")).get("id")
            if not account_id or account_id in seen:
                raise ValueError("Account IDs must be present and unique")
            seen.add(account_id)
            for field in ("positions", "orders", "activities"):
                value = wrapper.get(field)
                if field == "activities" and isinstance(value, dict):
                    value = value.get("data")
                if not isinstance(value, list):
                    raise ValueError(f"Account {account_id}: {field} must be a successful list response")
            wrappers.append((username, wrapper))

    accounts_rows = []
    positions_rows = []
    orders_rows = []
    transactions_rows = []

    for username, acc_wrapper in wrappers:
        account = safe_dict(acc_wrapper.get("account"))
        balances_list = acc_wrapper.get("balances") or [{}]
        balances = balances_list[0] if isinstance(balances_list, list) else balances_list
        balances = safe_dict(balances)

        account_id = account.get("id") or str(uuid.uuid4())

        # ── Accounts ──────────────────────────
        accounts_rows.append({
            "id": account_id,
            "username": username,
            "name": account.get("name"),
            "number": account.get("number"),
            "institution_name": account.get("institution_name"),
            "account_type": safe_dict(account.get("meta")).get("type"),
            "status": safe_dict(account.get("meta")).get("accountStatus"),
            "is_paper": False,
            "currency": safe_currency(balances.get("currency")),
            "total_balance": balances.get("amount"),
            "cash": balances.get("cash"),
            "buying_power": balances.get("buying_power"),
            "created_date": parse_dt(account.get("created_date")),
            "last_synced": parse_dt(
                safe_dict(account.get("sync_status"))
                .get("holdings", {})
                .get("last_successful_sync")
            ),
        })

        # ── Positions ─────────────────────────
        for pos in acc_wrapper.get("positions", []):
            symbol = extract_symbol(pos.get("symbol"))
            if not symbol:
                continue

            units = float(pos.get("units") or 0)
            price = float(pos.get("price") or 0)
            avg = float(pos.get("average_purchase_price") or 0)

            positions_rows.append({
                "account_id": account_id,
                "symbol": symbol,
                "description": safe_dict(safe_dict(pos.get("symbol")).get("symbol")).get("description") or safe_dict(pos.get("symbol")).get("description"),
                "units": units,
                "price": price,
                "average_purchase_price": avg,
                "open_pnl": pos.get("open_pnl"),
                "market_value": units * price,
                "cost_basis": units * avg,
                "is_cash_equivalent": bool(pos.get("cash_equivalent")),
            })

        # ── Transactions ──────────────────────
        activities = acc_wrapper.get("activities", {})

        for txn in (activities.get("data", []) if isinstance(activities, dict) else activities):
            sym_obj = txn.get("symbol") or {}
            symbol = extract_symbol(sym_obj)

            transactions_rows.append({
                "id":              txn.get("id") or str(uuid.uuid5(uuid.NAMESPACE_URL, account_id + json.dumps(txn, sort_keys=True))),
                "account_id":      account_id,
                "symbol":          symbol,
                "type":            txn.get("type"),
                "action":          txn.get("option_type"),
                "description":     txn.get("description"),
                "amount":          txn.get("amount"),
                "price":           txn.get("price"),
                "units":           txn.get("units") or 0,
                "fee":             txn.get("fee"),
                "currency":        safe_dict(txn.get("currency")).get("code"),
                "trade_date":      parse_dt(txn.get("trade_date")),
                "settlement_date": parse_dt(txn.get("settlement_date")),
                "institution":     txn.get("institution"),
            })

        # ── Orders ────────────────────────────
        for order in acc_wrapper.get("orders", []):
            order_id = order.get("brokerage_order_id") or order.get("id")

            underlying_symbol = extract_symbol(order.get("universal_symbol")) or extract_symbol(order.get("symbol"))

            orders_rows.append({
                "id":                 order_id or str(uuid.uuid5(uuid.NAMESPACE_URL, account_id + json.dumps(order, sort_keys=True))),
                "account_id":         account_id,
                "status":             order.get("status"),
                "action":             order.get("action"),
                "order_type":         order.get("order_type"),
                "time_in_force":      order.get("time_in_force"),
                "underlying_symbol":  underlying_symbol,
                "total_quantity":     float(order.get("total_quantity") or 0),
                "filled_quantity":    float(order.get("filled_quantity") or 0),
                "execution_price":    float(order.get("execution_price") or 0),
                "time_placed":        parse_dt(order.get("time_placed")),
                "time_executed":      parse_dt(order.get("time_executed")),
            })

    owned_engine = create_engine(db_url) if db_url else None
    if owned_engine is None and not has_app_context():
        raise RuntimeError("Use an app context or pass db_url explicitly")
    engine = owned_engine if owned_engine is not None else db.engine
    try:
        with Session(engine) as session, session.begin():
            for row in accounts_rows:
                existing = session.get(Account, row["id"])
                if existing is not None and existing.username != row["username"]:
                    raise ValueError("Account ownership cannot change during sync")
                session.merge(Account(**row))
            session.flush()
            for model, rows in ((Position, positions_rows), (Order, orders_rows), (Activity, transactions_rows)):
                for account_id in seen:
                    session.query(model).filter_by(account_id=account_id).delete(synchronize_session=False)
                session.add_all(model(**row) for row in rows)
    finally:
        if owned_engine is not None:
            owned_engine.dispose()
    stats = dict(accounts=len(accounts_rows), positions=len(positions_rows),
                 orders=len(orders_rows), transactions=len(transactions_rows))
    if verbose:
        print(f"DB sync complete: {stats}")
    return stats


if __name__ == "__main__":
    from app import app
    with app.app_context():
        sync_export_to_db(verbose=True)
