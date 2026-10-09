"""
snaptrade_export_all.py
-----------------------
Exports full SnapTrade account data for ALL registered users in users.json.
Can also add a new user on the fly before exporting.
Saves to  data-export/snaptrade_full_export.json  (overwritten each run).

Usage:
    python snaptrade_export_all.py                        # export all users
    python snaptrade_export_all.py --user alice           # export one existing user
    python snaptrade_export_all.py --add                  # add new user (fill in NEW_USER block) + export them
    python snaptrade_export_all.py --add --export-all     # add new user + export everyone
"""

import json
import os
import argparse
from datetime import datetime, timezone
from pathlib import Path
from snaptrade_client import SnapTrade

# ── Config ────────────────────────────────────────────────────────────────────
SNAPTRADE_CLIENT_ID = os.environ.get("SNAPTRADE_CLIENT_ID", "")
SNAPTRADE_CONSUMER_KEY = os.environ.get("SNAPTRADE_CONSUMER_KEY", "")
USERS_DB_FILE          = Path(os.environ.get("USERS_DB_FILE", Path(__file__).resolve().parents[1] / "users.json"))
EXPORT_DIR             = Path(os.environ.get("ACCOUNT_DATA_DIR", Path(__file__).resolve().parents[1] / "data-export"))
EXPORT_FILE            = EXPORT_DIR / "snaptrade_full_export.json"

snaptrade = SnapTrade(
    client_id=SNAPTRADE_CLIENT_ID,
    consumer_key=SNAPTRADE_CONSUMER_KEY,
)

# ── ✏️  NEW USER — fill in before running with --add ─────────────────────────
NEW_USER = {
    "username": os.environ.get("SNAPTRADE_USERNAME", ""),
    "snaptrade_user_id": os.environ.get("SNAPTRADE_USER_ID", ""),
    "snaptrade_user_secret": os.environ.get("SNAPTRADE_USER_SECRET", ""),
}
# ─────────────────────────────────────────────────────────────────────────────

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_users() -> dict:
    if USERS_DB_FILE.exists():
        return json.loads(USERS_DB_FILE.read_text())
    return {}

def load_export() -> dict:
    if EXPORT_FILE.exists():
        data = json.loads(EXPORT_FILE.read_text())
        # Handle old format or malformed file
        if "users" not in data:
            data["users"] = []
        return data
    return {"exported_at": None, "total_users": 0, "users": []}

def safe_body(response):
    try:
        return response.body or []
    except Exception:
        return []


# ── Step 1 (optional): Add new user to users.json ────────────────────────────

def add_user(users: dict) -> dict:
    username = NEW_USER["username"]

    if not all(NEW_USER.values()) or username == "YOUR_USERNAME":
        print("  ✗  Set SNAPTRADE_USERNAME, SNAPTRADE_USER_ID and SNAPTRADE_USER_SECRET before using --add.")
        exit(1)

    if username in users:
        print(f"  ℹ  '{username}' already in users.json — skipping add.")
    else:
        users[username] = {
            "username":              username,
            "snaptrade_user_id":     NEW_USER["snaptrade_user_id"],
            "snaptrade_user_secret": NEW_USER["snaptrade_user_secret"],
            "account_connected":     True,
            "created_at":            datetime.now(timezone.utc).isoformat(),
        }
        USERS_DB_FILE.write_text(json.dumps(users, indent=2))
        print(f"  ✓  Added '{username}' to users.json")

    return users

def _fetch_all_activities(user_id: str, user_secret: str, acc_id: str) -> list:
    """Fetch all activities by paginating via offset/limit."""
    all_activities = []
    offset = 0
    limit = 100

    while True:
        try:
            raw_response = snaptrade.account_information.get_account_activities(
                user_id=user_id,
                user_secret=user_secret,
                account_id=acc_id,
                offset=offset,
                limit=limit,
            )
            result = safe_body(raw_response)
        except Exception as exc:
            print(f"        ⚠  get_account_activities offset {offset} failed: {exc}")
            raise

        if not result:
            break

        rows = result if isinstance(result, list) else result.get("data", [])

        if not rows:
            break

        all_activities.extend(rows)
        print(f"        activities offset {offset}: +{len(rows)} rows (total {len(all_activities)})")

        if len(rows) < limit:
            break

        offset += limit

    print(f"        TOTAL activities fetched: {len(all_activities)}")
    return all_activities
# ── Step 2: Export one user via API ──────────────────────────────────────────

def export_user(username: str, user_id: str, user_secret: str) -> dict:
    print(f"  → Fetching accounts for '{username}' …")

    accounts_raw = safe_body(
        snaptrade.account_information.list_user_accounts(
            user_id=user_id, user_secret=user_secret
        )
    )

    accounts_out = []
    for acc in (accounts_raw or []):
        acc_id   = acc.get("id", "")
        acc_name = acc.get("name", "Unnamed")
        print(f"      account: {acc_name} ({acc_id})")

        def _fetch(fn, **kwargs):
            try:
                return safe_body(fn(user_id=user_id, user_secret=user_secret, account_id=acc_id, **kwargs))
            except Exception as exc:
                print(f"        ⚠  {fn.__name__} failed: {exc}")
                raise

        accounts_out.append({
            "account":    acc,
            "details":    _fetch(snaptrade.account_information.get_user_account_details),
            "balances":   _fetch(snaptrade.account_information.get_user_account_balance),
            "positions":  _fetch(snaptrade.account_information.get_user_account_positions),
            "orders":     _fetch(snaptrade.account_information.get_user_account_orders),
            "activities": _fetch_all_activities(user_id, user_secret, acc_id),
        })

    return {
        "username":          username,
        "snaptrade_user_id": user_id,
        "exported_at":       datetime.now(timezone.utc).isoformat(),
        "accounts":          accounts_out,
    }


# ── Step 3: Save exports to JSON ─────────────────────────────────────────────

def save_exports(user_exports: list[dict]):
    EXPORT_DIR.mkdir(exist_ok=True)
    export = load_export()
    existing = {u["username"]: i for i, u in enumerate(export["users"])}

    for user_export in user_exports:
        uname = user_export["username"]
        if uname in existing:
            export["users"][existing[uname]] = user_export
        else:
            export["users"].append(user_export)

    export["total_users"] = len(export["users"])
    export["exported_at"] = datetime.now(timezone.utc).isoformat()

    temporary = EXPORT_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(export, indent=2))
    temporary.replace(EXPORT_FILE)
    print(f"\n✓  Export saved → {EXPORT_FILE}  ({len(user_exports)} user(s) updated, {export['total_users']} total)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export SnapTrade data, optionally adding a new user first")
    parser.add_argument("--add",        action="store_true", help="Add the NEW_USER block to users.json before exporting")
    parser.add_argument("--export-all", action="store_true", help="When used with --add, export all users not just the new one")
    parser.add_argument("--user",       help="Export a single existing username only")
    args = parser.parse_args()

    if not SNAPTRADE_CLIENT_ID or not SNAPTRADE_CONSUMER_KEY:
        raise ValueError("Set SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY")
    users = load_users()

    # -- Add new user if requested
    if args.add:
        print("\n── Adding new user ──")
        users = add_user(users)

    # -- Determine who to export
    if args.add and not args.export_all:
        # Only export the newly added user
        target_username = NEW_USER["username"]
        target_users = {target_username: users[target_username]}
    elif args.user:
        # Export a single named user — fall back to NEW_USER block if not in users.json
        if args.user not in users:
            if NEW_USER["username"] == args.user and NEW_USER["snaptrade_user_id"] != "YOUR_SNAPTRADE_USER_ID":
                print(f"  ℹ  '{args.user}' not in users.json — using NEW_USER block credentials.")
                users = add_user(users)
            else:
                print(f"✗  User '{args.user}' not found in users.json and NEW_USER block doesn't match.")
                print(f"   Either add them to users.json or fill in the NEW_USER block at the top of this script.")
                raise ValueError("Unknown user")
        target_users = {args.user: users[args.user]}
    else:
        # Export everyone
        target_users = users

    if not target_users:
        raise ValueError("No users to export")

    print(f"\n── Exporting {len(target_users)} user(s) ──")
    user_exports = []
    for username, user_data in target_users.items():
        uid    = user_data.get("snaptrade_user_id")
        secret = user_data.get("snaptrade_user_secret")
        if not uid or not secret:
            print(f"  ⚠  Skipping '{username}': missing SnapTrade credentials")
            raise ValueError("Missing SnapTrade user credentials")
        try:
            user_exports.append(export_user(username, uid, secret))
        except Exception as exc:
            raise RuntimeError(f"Failed to export {username}") from exc

    save_exports(user_exports)


if __name__ == "__main__":
    main()