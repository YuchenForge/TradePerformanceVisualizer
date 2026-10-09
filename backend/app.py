from flask import Flask, jsonify, request, session, Response
from flask_cors import CORS
from functools import wraps
import os
from datetime import datetime, timedelta
from snaptrade_client import SnapTrade
import json
import yfinance as yf
import logging
import hashlib
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import re
import glob
import subprocess
import sys
import threading

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app, supports_credentials=True, origins=['http://localhost:3000', 'http://localhost:3001', 'http://127.0.0.1:3000', 'http://127.0.0.1:3001', 'http://18.221.160.103:5001', 'http://18.221.160.103:3000', 'http://bfi.duckdns.org'])

# Configuration
SNAPTRADE_CLIENT_ID = os.environ.get("SNAPTRADE_CLIENT_ID", "")
SNAPTRADE_CONSUMER_KEY = os.environ.get("SNAPTRADE_CONSUMER_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Basic Auth for the internal SnapTrade data platform (/api/explorer/*,
# /api/db/*). This is a lightweight shared-credentials gate for the creators-only
# data explorer — NOT the user-facing dashboard, which uses session auth.
# Override the defaults via env vars EXPLORER_USER / EXPLORER_PASS.
# ─────────────────────────────────────────────────────────────────────────────
EXPLORER_USER = os.environ.get('EXPLORER_USER', 'admin')
EXPLORER_PASS = os.environ.get('EXPLORER_PASS', '')


def require_basic_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not EXPLORER_PASS:
            return jsonify({'success': False, 'error': 'Set EXPLORER_PASS to enable the explorer'}), 503
        auth = request.authorization
        if not auth or auth.username != EXPLORER_USER or auth.password != EXPLORER_PASS:
            return Response(
                'Authentication required.', 401,
                {'WWW-Authenticate': 'Basic realm="SnapTrade Data Platform"'}
            )
        return f(*args, **kwargs)
    return decorated


# Database files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_DB_FILE = os.environ.get('USERS_DB_FILE', os.path.join(BASE_DIR, 'users.json'))
ACCOUNT_DATA_DIR = os.environ.get('ACCOUNT_DATA_DIR', os.path.join(BASE_DIR, 'data-export'))
ACCOUNT_DATA_FILE = os.path.join(ACCOUNT_DATA_DIR, 'account-data.json')

DATABASE_DIR = os.path.join(os.path.dirname(__file__), 'database')

from database.models import db, Account, Position, Order, Activity
from sqlalchemy import text
from database.db_sync import sync_export_to_db

# PostgreSQL: set DATABASE_URL=postgresql+psycopg://user:pass@localhost/dbname
# Local dev default uses SQLite (no Postgres install required).
_default_db_path = os.path.join(DATABASE_DIR, 'portfolio.db')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    f'sqlite:///{_default_db_path}'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Create tables on startup
with app.app_context():
    db.create_all()

# Ensure data-export directory exists
os.makedirs(ACCOUNT_DATA_DIR, exist_ok=True)

def load_users():
    """Load users from JSON file"""
    if os.path.exists(USERS_DB_FILE):
        try:
            with open(USERS_DB_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_users(users):
    """Save users to JSON file"""
    with open(USERS_DB_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def load_account_data():
    """Load account data from JSON file"""
    if os.path.exists(ACCOUNT_DATA_FILE):
        try:
            with open(ACCOUNT_DATA_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_account_data(account_data):
    """Save account data to JSON file"""
    with open(ACCOUNT_DATA_FILE, 'w') as f:
        json.dump(account_data, f, indent=2)

def hash_password(password):
    """Use a salted password hash for newly created accounts."""
    return generate_password_hash(password)


def verify_password(stored, password):
    if re.fullmatch(r'[0-9a-f]{64}', stored or ''):
        return secrets.compare_digest(stored, hashlib.sha256(password.encode()).hexdigest())
    return check_password_hash(stored or '', password)

def get_current_user():
    """Get current logged-in user from session"""
    if 'username' in session:
        users = load_users()
        return users.get(session['username'])
    return None

def get_snaptrade_client():
    """Get SnapTrade client with global credentials"""
    if not SNAPTRADE_CLIENT_ID or not SNAPTRADE_CONSUMER_KEY:
        raise RuntimeError('Set SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY')
    return SnapTrade(
        client_id=SNAPTRADE_CLIENT_ID,
        consumer_key=SNAPTRADE_CONSUMER_KEY
    )

def get_user_credentials():
    """Get user credentials for current user"""
    user = get_current_user()
    if user:
        return user.get('snaptrade_user_id'), user.get('snaptrade_user_secret')
    return None, None


def require_session(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated


def _get_stored_primary_account(username):
    stored = load_account_data().get(username, {})
    primary = stored.get('primary_account') or {}
    accounts = stored.get('accounts') or []
    if not primary and accounts:
        primary = accounts[0]
    return primary


def _extract_symbol(obj, default='N/A'):
    """Extract ticker from a SnapTrade position or activity."""
    raw = obj.get('symbol')
    if isinstance(raw, dict):
        inner = raw.get('symbol')
        if isinstance(inner, dict):
            return inner.get('symbol') or inner.get('raw_symbol') or default
        val = inner or raw.get('raw_symbol')
        return val if val else default
    if raw:
        return str(raw)
    return default


def _load_full_export() -> dict:
    """Read the full export JSON; fall back to the newest dated export snapshot."""
    candidates = []
    export_file = os.path.join(ACCOUNT_DATA_DIR, "snaptrade_full_export.json")
    if os.path.exists(export_file):
        candidates.append(export_file)
    dated_exports = sorted(
        glob.glob(os.path.join(ACCOUNT_DATA_DIR, "snaptrade_full_export*.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    for path in dated_exports:
        if path not in candidates:
            candidates.append(path)

    for path in candidates:
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if data:
                return data
        except Exception as exc:
            print(f"[export] Could not read {path}: {exc}")
    return {}


WATCHLIST = [
    'AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','BRK.B','JPM','V',
    'XOM','UNH','JNJ','PG',
    'SPY','QQQ','VOO','VTI','IWM','DIA'
]
QUOTE_CACHE = {}
QUOTE_TTL_SECONDS = 60

# Reduce noisy yfinance logging
try:
    logging.getLogger('yfinance').setLevel(logging.ERROR)
except Exception:
    pass


def _flat_portfolio_history(total_value, chart_start, end_day=None):
    """Flat line fallback from chart_start through end_day."""
    end_day = end_day or datetime.now().date()
    if isinstance(chart_start, datetime):
        chart_start = chart_start.date()
    base = float(total_value or 0)
    history = []
    day = chart_start
    while day <= end_day:
        history.append({'date': day.isoformat(), 'value': round(base, 2)})
        day += timedelta(days=1)
    return history


def _resolve_history_window(days_param, first_date=None):
    """Return (days_for_api, show_all, chart_start_date)."""
    end_day = datetime.now().date()
    if isinstance(first_date, datetime):
        first_date = first_date.date()

    if str(days_param).strip().lower() == 'all':
        chart_start = first_date or (end_day - timedelta(days=365 * 5))
        days = max((end_day - chart_start).days + 1, 1)
        return days, True, chart_start

    days = max(int(days_param), 1)
    window_start = end_day - timedelta(days=max(days - 1, 0))
    chart_start = max(window_start, first_date) if first_date else window_start
    return days, False, chart_start


def _get_account_first_transaction_date(account_id):
    if not account_id:
        return None
    try:
        user_id, user_secret = get_user_credentials()
        if not user_id or not user_secret:
            return None
        snaptrade_client = get_snaptrade_client()
        response = snaptrade_client.account_information.get_user_account_details(
            user_id=user_id,
            user_secret=user_secret,
            account_id=account_id,
        )
        details = response.body if response.body else {}
        first = (details.get('sync_status') or {}).get('transactions', {}).get('first_transaction_date')
        if first:
            return datetime.strptime(str(first)[:10], '%Y-%m-%d').date()
    except Exception as e:
        print(f"Error getting first transaction date: {e}")
    return None


def _fetch_account_activities_range(snaptrade_client, user_id, user_secret, account_id, start_date, end_date):
    """Paginated account activities for a date range (see test_snaptrade copy.py)."""
    if not account_id:
        return []

    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()

    activities = []
    for year in range(start_date.year, end_date.year + 1):
        year_start = max(start_date, datetime(year, 1, 1).date())
        year_end = min(end_date, datetime(year, 12, 31).date())
        start_str = year_start.strftime('%Y-%m-%d')
        end_str = year_end.strftime('%Y-%m-%d')
        offset = 0
        limit = 1000

        while True:
            resp = snaptrade_client.account_information.get_account_activities(
                account_id=account_id,
                user_id=user_id,
                user_secret=user_secret,
                start_date=start_str,
                end_date=end_str,
                offset=offset,
                limit=limit,
            )
            batch = _snaptrade_activities_page_items(getattr(resp, 'body', None))
            if not batch:
                break
            activities.extend(batch)
            if len(batch) < limit:
                break
            offset += limit

    return activities


def _activity_dt(activity):
    return _parse_iso_dt(activity.get('trade_date') or activity.get('settlement_date'))


def _build_portfolio_history_from_activities(activities, chart_start, selected_symbols=None):
    """
    Rebuild portfolio value over time from SnapTrade account activities.
    Only includes dates on/after the first activity (no pre-trade history).
    """
    if not activities:
        return []

    sorted_acts = sorted(activities, key=lambda a: _activity_dt(a) or datetime.min)
    first_dt = _activity_dt(sorted_acts[0])
    if not first_dt:
        return []

    end_day = datetime.now().date()
    first_day = first_dt.date()
    if isinstance(chart_start, datetime):
        chart_start = chart_start.date()
    chart_start = max(chart_start, first_day)

    holdings = {}
    cash = 0.0
    timeline = []

    for activity in sorted_acts:
        act_dt = _activity_dt(activity)
        if not act_dt:
            continue

        cash += _to_float(activity.get('amount', 0), 0.0)
        if not activity.get('option_symbol'):
            sym = _extract_symbol(activity, default=None)
            units = _to_float(activity.get('units', activity.get('quantity', 0)), 0.0)
            act_type = (activity.get('type') or '').upper()
            if sym and sym != 'N/A' and units != 0 and act_type in ('BUY', 'SELL', 'JNLS'):
                holdings[sym] = holdings.get(sym, 0.0) + units
                if abs(holdings.get(sym, 0.0)) < 1e-9:
                    holdings.pop(sym, None)

        timeline.append((act_dt.date(), dict(holdings), cash))

    if not timeline:
        return []

    symbols = set()
    for _, day_holdings, _ in timeline:
        symbols.update(day_holdings.keys())
    if selected_symbols:
        symbols = {sym for sym in symbols if sym in selected_symbols}

    price_series = {}
    # Fetch prices before chart_start so forward-fill has prior closes on the first days
    # of the window (avoids cash-only undervaluation and sudden jumps on 3M/6M views).
    price_fetch_start = chart_start - timedelta(days=30)
    if first_day:
        price_fetch_start = min(price_fetch_start, first_day)
    start_fetch = datetime.combine(price_fetch_start, datetime.min.time())
    end_fetch = datetime.combine(end_day, datetime.min.time())
    for sym in symbols:
        price_series[sym] = _fetch_symbol_daily_closes(sym, start_fetch, end_fetch)

    last_holdings = {}
    last_cash = 0.0
    timeline_idx = 0
    history = []
    day = chart_start

    while day <= end_day:
        while timeline_idx < len(timeline) and timeline[timeline_idx][0] <= day:
            _, last_holdings, last_cash = timeline[timeline_idx]
            timeline_idx += 1

        active_holdings = last_holdings
        if selected_symbols:
            active_holdings = {sym: qty for sym, qty in last_holdings.items() if sym in selected_symbols}

        day_str = day.isoformat()
        stock_value = 0.0
        for sym, qty in active_holdings.items():
            if qty <= 0:
                continue
            closes = price_series.get(sym, {})
            price = closes.get(day_str)
            if price is None:
                for prior_day in sorted(closes.keys(), reverse=True):
                    if prior_day <= day_str:
                        price = closes[prior_day]
                        break
            if price is not None:
                stock_value += price * qty

        total = stock_value if selected_symbols else (last_cash + stock_value)
        if day >= first_day:
            history.append({'date': day_str, 'value': round(total, 2)})
        day += timedelta(days=1)

    return history


def _fetch_symbol_daily_closes(symbol, start_date, end_date):
    """Return {YYYY-MM-DD: close_price} for one symbol."""
    yn = _normalize_symbol_for_yf(symbol)
    try:
        hist = yf.Ticker(yn).history(
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=True,
            interval='1d'
        )
        if hist is None or len(hist) == 0:
            return {}

        close_col = 'Close' if 'Close' in hist.columns else 'Adj Close'
        closes = {}
        for date, row in hist.iterrows():
            price = row.get(close_col)
            if price is None:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if price != price:
                continue
            closes[date.strftime('%Y-%m-%d')] = price
        return closes
    except Exception as e:
        print(f"Error fetching daily data for {symbol}: {e}")
        return {}


def _yf_history_points(symbol, start_date, end_date):
    """Return [{date, value}, ...] from Yahoo daily closes."""
    closes = _fetch_symbol_daily_closes(symbol, start_date, end_date)
    return [{'date': day, 'value': price} for day, price in sorted(closes.items())]


def get_account_id():
    """Get the primary SnapTrade account ID for the logged-in user."""
    try:
        user = get_current_user()
        if user:
            account_data = load_account_data()
            user_accounts = account_data.get(user['username'], {}).get('accounts', [])
            if user_accounts:
                return user_accounts[0].get('id')

        user_id, user_secret = get_user_credentials()
        if user_id and user_secret:
            snaptrade_client = get_snaptrade_client()
            accounts_response = snaptrade_client.account_information.list_user_accounts(
                user_id=user_id,
                user_secret=user_secret,
            )
            accounts = accounts_response.body if accounts_response.body else []
            if accounts:
                return accounts[0].get('id')
    except Exception as e:
        print(f"Error getting account ID: {e}")
    return None


def get_portfolio_positions():
    """Get positions from live SnapTrade API."""
    try:
        account_id = get_account_id()
        user_id, user_secret = get_user_credentials()
        if account_id and user_id and user_secret:
            snaptrade_client = get_snaptrade_client()
            positions_response = snaptrade_client.account_information.get_user_account_positions(
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
            )
            return positions_response.body if positions_response.body else []
    except Exception as e:
        print(f"Error getting positions: {e}")
    return []


def _compute_portfolio_summary(positions=None):
    """Build portfolio summary dict from SnapTrade positions."""
    if positions is None:
        positions = get_portfolio_positions()

    total_value = 0
    total_cost = 0
    holdings = []
    position_rows = []

    for position in positions:
        norm_symbol = _extract_symbol(position)
        if not norm_symbol or norm_symbol == 'N/A':
            continue

        current_price = position.get('price', 0)
        quantity = position.get('units', 0)
        avg_cost = position.get('average_purchase_price', 0)

        market_value = current_price * quantity
        cost_basis = avg_cost * quantity

        total_value += market_value
        total_cost += cost_basis

        position_rows.append({
            'position': position,
            'symbol': norm_symbol,
            'name': _get_name_from_position(position, norm_symbol),
            'price': current_price,
            'quantity': quantity,
            'marketValue': market_value,
            'costBasis': cost_basis,
        })

    symbols = [row['symbol'] for row in position_rows]
    previous_closes = _fetch_previous_closes(symbols)
    allocation_metadata = _fetch_allocation_metadata(symbols)

    for row in position_rows:
        position = row.pop('position')
        current_price = row['price']
        quantity = row['quantity']
        avg_cost = row['costBasis'] / quantity if quantity else 0
        prev_close = previous_closes.get(row['symbol'])
        daily_change_percent = 0.0
        daily_gain_loss = 0.0
        if prev_close and prev_close > 0:
            daily_change_percent = ((current_price - prev_close) / prev_close) * 100
            daily_gain_loss = (current_price - prev_close) * quantity

        meta = allocation_metadata.get(row['symbol'], {})
        holdings.append({
            **row,
            'change': ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0,
            'dailyChange': daily_change_percent,
            'dailyGainLoss': daily_gain_loss,
            'assetType': _asset_type_from_position(
                position, row['symbol'], meta.get('assetType')
            ),
            'sector': meta.get('sector') or 'Unknown',
        })

    account_total = _get_account_total_balance()
    if account_total is not None:
        cash_balance = max(0.0, account_total - total_value)
    else:
        cached_total = _get_cached_account_balance()
        cash_balance = max(0.0, cached_total - total_value) if cached_total is not None else 0.0

    total_gain_loss = total_value - total_cost
    total_return = (total_gain_loss / total_cost * 100) if total_cost > 0 else 0

    return {
        'totalValue': total_value,
        'totalCost': total_cost,
        'totalGainLoss': total_gain_loss,
        'totalReturn': total_return,
        'cashBalance': cash_balance,
        'holdings': holdings,
    }


def _positions_to_stocks(positions):
    stocks = []
    seen_symbols = set()
    for position in positions:
        sym = _extract_symbol(position)
        if not sym or sym == 'N/A' or sym in seen_symbols:
            continue
        quantity = position.get('units', 0)
        current_price = position.get('price', 0)
        stocks.append({
            'symbol': sym,
            'name': _get_name_from_position(position, sym),
            'quantity': quantity,
            'marketValue': current_price * quantity,
        })
        seen_symbols.add(sym)
    return stocks


@app.route('/api/portfolio/summary', methods=['GET'])
@require_session
def get_portfolio_summary():
    """Get portfolio summary with total value and performance"""
    try:
        return jsonify({
            'success': True,
            'data': _compute_portfolio_summary(),
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/auth/register', methods=['POST'])
def register():
    """Register a new user account and create Snaptrade user"""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'A JSON object is required'}), 400
        if not isinstance(data.get('username', ''), str) or not isinstance(data.get('password', ''), str):
            return jsonify({'success': False, 'error': 'Username and password must be strings'}), 400
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Username and password are required'}), 400
        
        users = load_users()
        if username in users:
            return jsonify({'success': False, 'error': 'Username already exists'}), 400
        
        # Step 1: Register user with Snaptrade
        snaptrade_client = get_snaptrade_client()
        try:
            register_response = snaptrade_client.authentication.register_snap_trade_user(
                body={'userId': username}
            )
            
            if not register_response.body:
                return jsonify({'success': False, 'error': 'Failed to register with Snaptrade'}), 500
            
            user_id = register_response.body.get('userId')
            user_secret = register_response.body.get('userSecret')
            
            if not user_id or not user_secret:
                return jsonify({'success': False, 'error': 'Invalid Snaptrade response'}), 500
            
        except Exception as e:
            return jsonify({'success': False, 'error': f'Snaptrade registration failed: {str(e)}'}), 500
        
        # Step 2: Generate login URL for brokerage connection
        try:
            login_response = snaptrade_client.authentication.login_snap_trade_user(
                user_id=user_id,
                user_secret=user_secret
            )
            
            if not login_response.body:
                return jsonify({'success': False, 'error': 'Failed to generate connection URL'}), 500
            
            redirect_uri = login_response.body.get('redirectURI')
            session_id = login_response.body.get('sessionId')
            
            if not redirect_uri:
                return jsonify({'success': False, 'error': 'Invalid connection URL response'}), 500
            
        except Exception as e:
            return jsonify({'success': False, 'error': f'Failed to generate connection URL: {str(e)}'}), 500
        
        # Step 3: Save user data (without logging them in yet)
        users[username] = {
            'username': username,
            'password_hash': hash_password(password),
            'snaptrade_user_id': user_id,
            'snaptrade_user_secret': user_secret,
            'redirect_uri': redirect_uri,
            'session_id': session_id,
            'account_connected': False,
            'created_at': datetime.now().isoformat()
        }
        save_users(users)
        session['pending_setup'] = username
        
        return jsonify({
            'success': True,
            'message': 'Account created successfully',
            'redirectURI': redirect_uri,
            'sessionId': session_id
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/auth/complete-setup', methods=['POST'])
def complete_setup():
    """Complete account setup by fetching and storing account data"""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'A JSON object is required'}), 400
        if not isinstance(data.get('username', ''), str) or not isinstance(data.get('password', ''), str):
            return jsonify({'success': False, 'error': 'Username and password must be strings'}), 400
        username = data.get('username', '').strip()
 
        if not username:
            return jsonify({'success': False, 'error': 'Username is required'}), 400
 
        users = load_users()
        user = users.get(username)
 
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
 
        user_id     = user.get('snaptrade_user_id')
        user_secret = user.get('snaptrade_user_secret')
 
        if not user_id or not user_secret:
            return jsonify({'success': False, 'error': 'Invalid user credentials'}), 400
 
        if session.get('pending_setup') != username and session.get('username') != username:
            return jsonify({'success': False, 'error': 'Not authorized to complete this account'}), 403

        # ── existing: fetch account list from SnapTrade ───────────────────────
        snaptrade_client = get_snaptrade_client()
        try:
            accounts_response = snaptrade_client.account_information.list_user_accounts(
                user_id=user_id,
                user_secret=user_secret
            )
            if not accounts_response.body:
                return jsonify({'success': False,
                                'error': 'No accounts found. Please connect your brokerage account first.'}), 400
            accounts = accounts_response.body
        except Exception as e:
            return jsonify({'success': False, 'error': f'Failed to fetch accounts: {str(e)}'}), 500
 
        # ── existing: store light account data to JSON file ───────────────────
        if len(accounts) > 0:
            account_id = accounts[0].get('id')
            try:
                account_details_response = snaptrade_client.account_information.get_user_account_details(
                    account_id=account_id,
                    user_id=user_id,
                    user_secret=user_secret
                )
                account_details = account_details_response.body if account_details_response.body else accounts[0]
            except Exception as e:
                print(f"Error fetching account details: {e}")
                account_details = accounts[0]
        else:
            account_details = None
 
        account_data = load_account_data()
        account_data[username] = {
            'username': username,
            'accounts': accounts,
            'primary_account': account_details,
            'fetched_at': datetime.now().isoformat()
        }
        save_account_data(account_data)
 
        users[username]['account_connected'] = True
        users[username]['accounts_fetched_at'] = datetime.now().isoformat()
        save_users(users)
 
        # Export the connected user's current data before importing it.
        database_synced = False
        try:
            subprocess.run([sys.executable, os.path.join(DATABASE_DIR, 'snaptrade_export_all.py'),
                            '--user', username], timeout=120, check=True, cwd=BASE_DIR)
            sync_export_to_db(export_file=os.path.join(ACCOUNT_DATA_DIR, "snaptrade_full_export.json"))
            database_synced = True
        except Exception:
            app.logger.exception("Account connected, but database sync failed")

        return jsonify({
            'success': True,
            'message': 'Account setup completed successfully',
            'database_synced': database_synced,
            'accounts': accounts
        })
 
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login user"""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'A JSON object is required'}), 400
        if not isinstance(data.get('username', ''), str) or not isinstance(data.get('password', ''), str):
            return jsonify({'success': False, 'error': 'Username and password must be strings'}), 400
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Username and password are required'}), 400
        
        users = load_users()
        user = users.get(username)
        
        if not user or not verify_password(user.get('password_hash'), password):
            return jsonify({'success': False, 'error': 'Invalid username or password'}), 401
        
        # Check if account is connected
        if not user.get('account_connected', False):
            return jsonify({
                'success': False,
                'error': 'Account setup not completed. Please complete brokerage connection.',
                'needs_setup': True
            }), 403
        
        if re.fullmatch(r'[0-9a-f]{64}', user.get('password_hash', '')):
            user['password_hash'] = hash_password(password)
            save_users(users)
        session.clear()
        session['username'] = username
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'username': username
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Logout user"""
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Check if user is logged in"""
    user = get_current_user()
    if user:
        return jsonify({
            'success': True,
            'logged_in': True,
            'username': user['username']
        })
    return jsonify({
        'success': True,
        'logged_in': False
    })


def _mask_account_number(number):
    if not number:
        return None
    text = str(number)
    if len(text) <= 4:
        return text
    return f"••••{text[-4:]}"


def _build_user_profile(user):
    username = user.get('username')
    profile = {
        'username': username,
        'createdAt': user.get('created_at'),
        'accountConnected': bool(user.get('account_connected', False)),
        'accountsFetchedAt': user.get('accounts_fetched_at'),
        'brokerage': None,
    }

    primary = _get_stored_primary_account(username)

    if not primary:
        user_id, user_secret = get_user_credentials()
        account_id = get_account_id()
        if user_id and user_secret and account_id:
            try:
                snaptrade_client = get_snaptrade_client()
                response = snaptrade_client.account_information.get_user_account_details(
                    user_id=user_id,
                    user_secret=user_secret,
                    account_id=account_id,
                )
                primary = response.body if response.body else {}
            except Exception as e:
                print(f"Error fetching profile account details: {e}")

    if primary:
        balance = primary.get('balance') or {}
        total = balance.get('total') or {}
        sync_status = primary.get('sync_status') or {}
        holdings_sync = sync_status.get('holdings') or {}
        transactions_sync = sync_status.get('transactions') or {}
        meta = primary.get('meta') or {}
        total_balance = _to_float(total.get('amount'), None)

        profile['brokerage'] = {
            'institutionName': primary.get('institution_name') or meta.get('institution_name'),
            'accountName': primary.get('name'),
            'accountNumber': _mask_account_number(primary.get('number')),
            'accountType': meta.get('type'),
            'status': primary.get('status') or meta.get('status'),
            'isPaper': bool(primary.get('is_paper', False)),
            'balance': total_balance,
            'currency': total.get('currency') or 'USD',
            'lastHoldingsSync': holdings_sync.get('last_successful_sync'),
            'lastTransactionsSync': transactions_sync.get('last_successful_sync'),
            'firstTransactionDate': transactions_sync.get('first_transaction_date'),
        }

    return profile


@app.route('/api/auth/profile', methods=['GET'])
@require_session
def auth_profile():
    """Return safe account profile details for the logged-in user."""
    return jsonify({
        'success': True,
        'data': _build_user_profile(get_current_user()),
    })


def _parse_transaction_description(description, symbol):
    """Parse transaction description to separate action type from details"""
    if not description:
        return 'Transaction', description
    
    # Common patterns in descriptions
    desc_upper = description.upper()
    
    # Check for BUY variations
    if 'BUY PARTIAL_FILL' in desc_upper:
        action = 'BUY PARTIAL_FILL'
    elif 'BUY FILL' in desc_upper:
        action = 'BUY FILL'
    elif 'BUY' in desc_upper:
        action = 'BUY'
    # Check for SELL variations
    elif 'SELL PARTIAL_FILL' in desc_upper:
        action = 'SELL PARTIAL_FILL'
    elif 'SELL FILL' in desc_upper:
        action = 'SELL FILL'
    elif 'SELL' in desc_upper:
        action = 'SELL'
    # Check for other types
    elif 'DEPOSIT' in desc_upper or 'JNLC' in desc_upper:
        action = 'DEPOSIT'
    elif 'WITHDRAWAL' in desc_upper or 'JNLS' in desc_upper:
        action = 'WITHDRAWAL'
    elif 'FEE' in desc_upper:
        action = 'FEE'
    elif 'DIVIDEND' in desc_upper or 'DIV' in desc_upper:
        action = 'DIVIDEND'
    elif 'INTEREST' in desc_upper or 'INT' in desc_upper:
        action = 'INTEREST'
    else:
        action = 'Transaction'
    
    # Extract price if present (e.g., "at 612.17")
    price_match = None
    price_pattern = r'at\s+([\d,.]+)'
    match = re.search(price_pattern, description, re.IGNORECASE)
    if match:
        price_match = match.group(1)
    
    return action, price_match


def _to_float(value, default=0.0):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def _normalize_option_metadata(activity):
    option_symbol = activity.get('option_symbol')
    if not isinstance(option_symbol, dict):
        return {
            'isOption': False,
            'underlyingSymbol': None,
            'strikePrice': None,
            'optionType': None,
            'expirationDate': None,
            'contractCount': 0.0,
            'positionSide': None
        }

    units = _to_float(activity.get('units', activity.get('quantity', 0)), 0.0)
    underlying = option_symbol.get('underlying_symbol')
    underlying_symbol = None
    if isinstance(underlying, dict):
        underlying_symbol = underlying.get('symbol') or underlying.get('raw_symbol')

    option_type = (option_symbol.get('option_type') or '').upper()
    if option_type not in ('CALL', 'PUT'):
        option_type = None

    position_side = None
    if units > 0:
        position_side = 'LONG'
    elif units < 0:
        position_side = 'SHORT'

    return {
        'isOption': True,
        'underlyingSymbol': underlying_symbol,
        'strikePrice': _to_float(option_symbol.get('strike_price'), None),
        'optionType': option_type,
        'expirationDate': option_symbol.get('expiration_date'),
        'contractCount': abs(units),
        'positionSide': position_side
    }

def _normalize_type_label(raw_type, action, is_option):
    type_upper = (raw_type or '').upper()
    action_upper = (action or '').upper()

    if type_upper == 'JNLC':
        return 'DEPOSIT'
    if type_upper == 'JNLS':
        return 'WITHDRAWAL'
    if type_upper in ('FEE', 'FEES'):
        return 'FEE'
    if type_upper in ('DIV', 'DIVIDEND'):
        return 'DIVIDEND'
    if type_upper in ('INT', 'INTEREST'):
        return 'INTEREST'

    if is_option:
        if 'EXERCISE' in type_upper or 'EXERCISE' in action_upper:
            return 'EXERCISE'
        if 'ASSIGN' in type_upper or 'ASSIGN' in action_upper:
            return 'ASSIGNMENT'
        if 'OPEN' in action_upper:
            return 'OPEN'
        if 'CLOSE' in action_upper:
            return 'CLOSE'

    return type_upper if type_upper else 'OTHER'

def _parse_iso_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None

def _option_key(option_type, strike_price, expiration_date, underlying_symbol):
    return f"{underlying_symbol or ''}|{option_type or ''}|{strike_price or ''}|{expiration_date or ''}"


def _should_include_option_order(order):
    """Only surface option orders that are active or have fills."""
    filled_qty = _to_float(order.get('filled_quantity', 0), 0.0)
    total_qty = _to_float(order.get('total_quantity', filled_qty), filled_qty)
    status = (order.get('status') or '').upper()
    active_statuses = {
        'PENDING', 'OPEN', 'NEW', 'ACCEPTED', 'QUEUED', 'WORKING',
        'PARTIALLY_FILLED', 'PARTIAL',
    }
    if filled_qty > 0:
        return True
    if status in ('EXECUTED', 'FILLED'):
        return filled_qty > 0
    if status in active_statuses and total_qty > 0:
        return True
    return False


def _append_option_order_row(rows, order, matched_fee=0.0):
    option_symbol = order.get('option_symbol')
    if not isinstance(option_symbol, dict):
        return False
    if not _should_include_option_order(order):
        return False

    underlying = option_symbol.get('underlying_symbol')
    underlying_symbol = None
    if isinstance(underlying, dict):
        underlying_symbol = underlying.get('symbol') or underlying.get('raw_symbol')

    option_type = (option_symbol.get('option_type') or '').upper()
    if option_type not in ('CALL', 'PUT'):
        option_type = None

    filled_qty = _to_float(order.get('filled_quantity', order.get('total_quantity', 0)), 0.0)
    total_qty = _to_float(order.get('total_quantity', filled_qty), filled_qty)
    action = (order.get('action') or '').upper()
    position_side = 'LONG' if 'BUY' in action else ('SHORT' if 'SELL' in action else None)
    limit_price = _to_float(order.get('limit_price'), 0.0)
    execution_price = _to_float(order.get('execution_price'), 0.0)
    status_value = order.get('status') or 'UNKNOWN'
    placed_time = order.get('time_placed')
    executed_time = order.get('time_executed')
    qty_for_display = filled_qty if filled_qty > 0 else total_qty

    order_amount = execution_price * filled_qty * 100
    if 'BUY' in action:
        order_amount = -abs(order_amount)
    elif 'SELL' in action:
        order_amount = abs(order_amount)

    rows.append({
        'id': f"order_{order.get('brokerage_order_id')}",
        'symbol': underlying_symbol or 'N/A',
        'type': 'OPTION_ORDER',
        'typeLabel': _normalize_type_label('OPTION_ORDER', action, True),
        'action': action or 'ORDER',
        'description': f"Order {status_value}",
        'priceFromDescription': None,
        'date': executed_time or placed_time or '',
        'tradeDate': executed_time,
        'settlementDate': None,
        'amount': order_amount,
        'fee': matched_fee,
        'netAmount': order_amount - matched_fee,
        'quantity': qty_for_display,
        'units': qty_for_display,
        'filledQuantity': filled_qty,
        'price': execution_price,
        'isOption': True,
        'underlyingSymbol': underlying_symbol,
        'strikePrice': _to_float(option_symbol.get('strike_price'), None),
        'optionType': option_type,
        'expirationDate': option_symbol.get('expiration_date'),
        'contractCount': qty_for_display,
        'positionSide': position_side,
        'limitPrice': limit_price,
        'executionPrice': execution_price,
        'placedTime': placed_time,
        'executedTime': executed_time,
        'feeBreakdown': {
            'occClearingFee': 0.0,
            'orfFee': 0.0,
            'otherFees': matched_fee
        },
        'status': status_value,
        'source': 'order'
    })
    return True


def _snaptrade_activities_page_items(page_body):
    """
    Account-level activities return PaginatedUniversalActivity: { "data": [...], "pagination": {...} }.
    User-level get_activities returns a plain list. Normalize to a list of activity dicts.
    """
    if page_body is None:
        return []
    if isinstance(page_body, list):
        return page_body
    if isinstance(page_body, dict):
        return page_body.get('data') or []
    data = getattr(page_body, 'data', None)
    if data is not None:
        return list(data) if not isinstance(data, list) else data
    return []


def _fetch_activities_body(snaptrade_client, user_id, user_secret, account_id):
    """
    Load universal activities for the dashboard transaction table.
    Prefer the account-scoped endpoint so the list matches a single brokerage
    account (same as positions and option orders). The user-level get_activities
    API returns activities across *all* connections for that SnapTrade user,
    which often explodes the count (e.g. hundreds) vs. what you see for one account.
    """
    if account_id:
        activities_body = []
        offset = 0
        page_size = 1000
        while True:
            resp = snaptrade_client.account_information.get_account_activities(
                account_id=account_id,
                user_id=user_id,
                user_secret=user_secret,
                offset=offset,
                limit=page_size,
            )
            page_body = getattr(resp, 'body', None)
            batch = _snaptrade_activities_page_items(page_body)
            if not batch:
                break
            activities_body.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return activities_body
    activities_response = snaptrade_client.transactions_and_reporting.get_activities(
        user_id=user_id,
        user_secret=user_secret
    )
    return getattr(activities_response, 'body', None) or []


@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    """Get recent transactions"""
    try:
        user_id, user_secret = get_user_credentials()
        if not user_id or not user_secret:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401
        
        snaptrade_client = get_snaptrade_client()
        account_id = get_account_id()
        transactions = []
        activities_body = _fetch_activities_body(
            snaptrade_client, user_id, user_secret, account_id
        )
        option_activity_candidates = []
        if activities_body:
            for activity in activities_body:
                norm_symbol = _extract_symbol(activity)
                
                description = activity.get('description', '')
                action, price_from_desc = _parse_transaction_description(description, norm_symbol)
                option_meta = _normalize_option_metadata(activity)
                amount = _to_float(activity.get('amount', 0), 0.0)
                fee = _to_float(activity.get('fee', 0), 0.0)
                units = _to_float(activity.get('units', activity.get('quantity', 0)), 0.0)
                price = _to_float(activity.get('price', 0), 0.0)
                type_label = _normalize_type_label(activity.get('type', 'Unknown'), action, option_meta['isOption'])
                
                transactions.append({
                    'id': activity.get('id'),
                    'symbol': norm_symbol,
                    'type': activity.get('type', 'Unknown'),
                    'typeLabel': type_label,
                    'action': action,
                    'description': description,
                    'priceFromDescription': price_from_desc,
                    'date': activity.get('trade_date', activity.get('settlement_date', '')),
                    'tradeDate': activity.get('trade_date'),
                    'settlementDate': activity.get('settlement_date'),
                    'amount': amount,
                    'fee': fee,
                    'netAmount': amount - fee,
                    'quantity': units,
                    'units': units,
                    'price': price,
                    'isOption': option_meta['isOption'],
                    'underlyingSymbol': option_meta['underlyingSymbol'],
                    'strikePrice': option_meta['strikePrice'],
                    'optionType': option_meta['optionType'],
                    'expirationDate': option_meta['expirationDate'],
                    'contractCount': option_meta['contractCount'],
                    'positionSide': option_meta['positionSide'],
                    'limitPrice': None,
                    'executionPrice': price,
                    'placedTime': activity.get('trade_date'),
                    'executedTime': activity.get('trade_date'),
                    'feeBreakdown': {
                        'occClearingFee': 0.0,
                        'orfFee': 0.0,
                        'otherFees': fee
                    },
                    'source': 'activity',
                    'status': 'Success'
                })
                if option_meta['isOption']:
                    option_activity_candidates.append({
                        'key': _option_key(
                            option_meta['optionType'],
                            option_meta['strikePrice'],
                            option_meta['expirationDate'],
                            option_meta['underlyingSymbol']
                        ),
                        'side': 'BUY' if amount < 0 else 'SELL',
                        'units': abs(units),
                        'price': price,
                        'fee': fee,
                        'dt': _parse_iso_dt(activity.get('trade_date') or activity.get('settlement_date'))
                    })

        # Option orders from primary account only (same account_id as activities above).
        seen_order_ids = set()
        if account_id:
            try:
                orders_response = snaptrade_client.account_information.get_user_account_orders(
                    user_id=user_id,
                    user_secret=user_secret,
                    account_id=account_id
                )
                orders_body = getattr(orders_response, 'body', None) or []
                option_order_rows = []
                for order in orders_body:
                    order_id = order.get('brokerage_order_id')
                    if order_id and order_id in seen_order_ids:
                        continue

                    option_symbol = order.get('option_symbol')
                    if not isinstance(option_symbol, dict):
                        continue
                    if not _should_include_option_order(order):
                        continue

                    filled_qty = _to_float(order.get('filled_quantity', order.get('total_quantity', 0)), 0.0)
                    total_qty = _to_float(order.get('total_quantity', filled_qty), filled_qty)
                    action = (order.get('action') or '').upper()
                    side = 'BUY' if 'BUY' in action else ('SELL' if 'SELL' in action else None)
                    execution_price = _to_float(order.get('execution_price'), 0.0)
                    placed_time = order.get('time_placed')
                    executed_time = order.get('time_executed')
                    order_dt = _parse_iso_dt(executed_time or placed_time)
                    option_type = (option_symbol.get('option_type') or '').upper()
                    if option_type not in ('CALL', 'PUT'):
                        option_type = None
                    underlying = option_symbol.get('underlying_symbol')
                    underlying_symbol = None
                    if isinstance(underlying, dict):
                        underlying_symbol = underlying.get('symbol') or underlying.get('raw_symbol')
                    key = _option_key(
                        option_type,
                        _to_float(option_symbol.get('strike_price'), None),
                        option_symbol.get('expiration_date'),
                        underlying_symbol
                    )
                    qty_for_display = filled_qty if filled_qty > 0 else total_qty
                    matched_fee = 0.0
                    best_delta = None
                    for candidate in option_activity_candidates:
                        if candidate['key'] != key or candidate['side'] != side:
                            continue
                        if qty_for_display > 0 and candidate['units'] > 0 and abs(candidate['units'] - qty_for_display) > 0.0001:
                            continue
                        if execution_price > 0 and candidate['price'] > 0 and abs(candidate['price'] - execution_price) > 0.02:
                            continue
                        if order_dt and candidate['dt']:
                            delta = abs((candidate['dt'] - order_dt).total_seconds())
                        else:
                            delta = 0
                        if best_delta is None or delta < best_delta:
                            best_delta = delta
                            matched_fee = candidate['fee']

                    if _append_option_order_row(option_order_rows, order, matched_fee):
                        if order_id:
                            seen_order_ids.add(order_id)

                transactions.extend(option_order_rows)
            except Exception as order_err:
                print(f"Error getting option orders: {order_err}")

        # Sort transactions by date (descending), then by time (descending), then by symbol (ascending)
        transactions.sort(key=lambda x: (
            -datetime.fromisoformat(x['date'].replace('Z', '+00:00')).timestamp() if x['date'] else 0,
            x['symbol']
        ))
        
        return jsonify({
            'success': True,
            'data': transactions
        })
    except Exception as e:
        print(f"Error getting transactions: {e}")
        return jsonify({
            'success': True,
            'data': []
        })


@app.route('/api/portfolio/stocks', methods=['GET'])
@require_session
def get_portfolio_stocks():
    """Get list of all stocks in portfolio for filtering"""
    try:
        return jsonify({
            'success': True,
            'data': _positions_to_stocks(get_portfolio_positions()),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/portfolio/history', methods=['GET'])
@require_session
def get_portfolio_history():
    """Return historical portfolio values from SnapTrade (not pre-trade yfinance guesses)."""
    try:
        days_param = request.args.get('days', 30)
        selected_symbols = request.args.get('symbols', '').split(',') if request.args.get('symbols') else []
        selected_symbols = [s.strip().upper() for s in selected_symbols if s.strip()]

        account_id = get_account_id()
        user_id, user_secret = get_user_credentials()
        first_date = _get_account_first_transaction_date(account_id) if account_id else None
        end_day = datetime.now().date()
        days, show_all, chart_start = _resolve_history_window(days_param, first_date)

        if account_id and user_id and user_secret:
            snaptrade_client = get_snaptrade_client()
            fetch_start = first_date or chart_start
            if isinstance(fetch_start, datetime):
                fetch_start = fetch_start.date()
            activities = _fetch_account_activities_range(
                snaptrade_client,
                user_id,
                user_secret,
                account_id,
                fetch_start,
                datetime.combine(end_day, datetime.max.time()),
            )
            activity_history = _build_portfolio_history_from_activities(
                activities,
                chart_start,
                selected_symbols or None,
            )
            if activity_history:
                return jsonify({
                    'success': True,
                    'data': activity_history,
                    'source': 'snaptrade_activities',
                    'timeframe': 'all' if show_all else days,
                })

        summary = _compute_portfolio_summary()
        total_value = summary.get('totalValue', 0)
        history = _flat_portfolio_history(total_value, chart_start, end_day)
        return jsonify({
            'success': True,
            'data': history,
            'source': 'flat_fallback',
            'timeframe': 'all' if show_all else days,
        })

    except Exception as e:
        print(f"Error in get_portfolio_history: {e}")
        end_day = datetime.now().date()
        return jsonify({
            'success': True,
            'data': _flat_portfolio_history(0, end_day, end_day),
            'source': 'flat_fallback'
        })


@app.route('/api/benchmark/history', methods=['GET'])
def get_benchmark_history():
    """Get historical data for benchmark"""
    try:
        symbol = _normalize_symbol_for_yf(request.args.get('symbol', 'SPY').upper())
        first_date = _get_account_first_transaction_date(get_account_id())
        days, _, chart_start = _resolve_history_window(request.args.get('days', 180), first_date)

        end_date = datetime.now()
        if isinstance(chart_start, datetime):
            start_date = chart_start
        else:
            start_date = datetime.combine(chart_start, datetime.min.time())

        history = _yf_history_points(symbol, start_date, end_date)
        if history:
            return jsonify({'success': True, 'data': history, 'symbol': symbol})

        return jsonify({'success': False, 'error': f'Could not fetch data for {symbol}'}), 500

    except Exception as e:
        print(f"Error in get_benchmark_history: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _get_name_from_position(position, sym):
    """Extract display name from a position object."""
    if position.get('description'):
        return position['description']
    symbol_obj = position.get('symbol')
    if isinstance(symbol_obj, dict):
        inner_symbol = symbol_obj.get('symbol')
        if isinstance(inner_symbol, dict):
            return inner_symbol.get('description', symbol_obj.get('description', sym))
        return symbol_obj.get('description', sym)
    return sym


def _get_inner_symbol_type(position):
    symbol_obj = position.get('symbol')
    if not isinstance(symbol_obj, dict):
        return {}
    inner_symbol = symbol_obj.get('symbol')
    if isinstance(inner_symbol, dict):
        type_obj = inner_symbol.get('type')
        return type_obj if isinstance(type_obj, dict) else {}
    type_obj = symbol_obj.get('type')
    return type_obj if isinstance(type_obj, dict) else {}


def _asset_type_from_position(position, symbol, yf_asset_type=None):
    if position.get('cash_equivalent'):
        return 'Cash Equivalent'
    type_obj = _get_inner_symbol_type(position)
    code = (type_obj.get('code') or '').lower()
    desc = (type_obj.get('description') or '').strip()
    code_map = {
        'cs': 'Stock',
        'ps': 'Stock',
        'ad': 'Stock',
        'et': 'ETF',
        'etf': 'ETF',
        'oef': 'Fund',
        'cef': 'Fund',
        'bnd': 'Bond',
        'ut': 'Unit',
        'crypto': 'Crypto',
        'cry': 'Crypto',
    }
    if code in code_map:
        return code_map[code]
    if desc:
        lower = desc.lower()
        if 'etf' in lower:
            return 'ETF'
        if 'common stock' in lower or lower == 'equity':
            return 'Stock'
        return desc
    if yf_asset_type:
        return yf_asset_type
    return 'Stock'


ALLOCATION_META_CACHE = {}
ALLOCATION_META_TTL_SECONDS = 300


def _fetch_allocation_metadata(symbols):
    """Return {symbol: {sector, assetType}} using yfinance with short-lived cache."""
    results = {}
    for symbol in symbols:
        if not isinstance(symbol, str):
            continue
        symbol = symbol.strip()
        if not symbol:
            continue
        cached = ALLOCATION_META_CACHE.get(symbol)
        if cached and (datetime.now() - cached['ts']).total_seconds() < ALLOCATION_META_TTL_SECONDS:
            results[symbol] = cached['data']
            continue

        meta = {'sector': 'Unknown', 'assetType': None}
        try:
            yn = _normalize_symbol_for_yf(symbol)
            info = yf.Ticker(yn).info or {}
            meta['sector'] = info.get('sector') or info.get('industry') or 'Unknown'
            quote_type = (info.get('quoteType') or '').upper()
            if quote_type == 'ETF':
                meta['assetType'] = 'ETF'
            elif quote_type == 'EQUITY':
                meta['assetType'] = 'Stock'
            elif quote_type in ('MUTUALFUND', 'MUTUAL_FUND'):
                meta['assetType'] = 'Fund'
        except Exception as e:
            print(f"Error fetching allocation metadata for {symbol}: {e}")

        ALLOCATION_META_CACHE[symbol] = {'ts': datetime.now(), 'data': meta}
        results[symbol] = meta
    return results


def _fetch_previous_closes(symbols):
    """Return {symbol: previous_trading_day_close} for daily change calculations."""
    results = {}
    for symbol in symbols:
        if not isinstance(symbol, str):
            continue
        symbol = symbol.strip()
        if not symbol or symbol in results:
            continue
        yn = _normalize_symbol_for_yf(symbol)
        try:
            hist = yf.Ticker(yn).history(period='5d', interval='1d', auto_adjust=True)
            if hist is None or len(hist) == 0:
                continue
            close_col = 'Close' if 'Close' in hist.columns else 'Adj Close'
            closes = hist[close_col].dropna()
            if len(closes) >= 2:
                results[symbol] = float(closes.iloc[-2])
            elif len(closes) == 1:
                results[symbol] = float(closes.iloc[0])
        except Exception as e:
            print(f"Error fetching previous close for {symbol}: {e}")
    return results


def _get_account_total_balance():
    try:
        account_id = get_account_id()
        user_id, user_secret = get_user_credentials()
        if not account_id or not user_id or not user_secret:
            return None

        snaptrade_client = get_snaptrade_client()
        response = snaptrade_client.account_information.get_user_account_details(
            user_id=user_id,
            user_secret=user_secret,
            account_id=account_id,
        )
        details = response.body if response.body else {}
        balance = details.get('balance') or {}
        total = balance.get('total') or {}
        return _to_float(total.get('amount'), None)
    except Exception as e:
        print(f"Error getting account balance: {e}")
        return None


def _get_cached_account_balance():
    """Read account total balance from account-data.json cache."""
    user = get_current_user()
    if not user:
        return None
    primary = _get_stored_primary_account(user['username'])
    total = (primary.get('balance') or {}).get('total') or {}
    return _to_float(total.get('amount'), None)


@app.route('/api/watchlist', methods=['GET'])
def get_watchlist():
    """Return the static watchlist symbols."""
    return jsonify({'success': True, 'data': WATCHLIST})


def _quote_from_cache(symbol: str):
    entry = QUOTE_CACHE.get(symbol)
    if not entry:
        return None
    if (datetime.now() - entry['ts']).total_seconds() > QUOTE_TTL_SECONDS:
        return None
    return entry['data']


def _store_quote_cache(symbol: str, data: dict):
    QUOTE_CACHE[symbol] = {'ts': datetime.now(), 'data': data}


def _mock_quote(symbol: str):
    base = 100 + (abs(hash(symbol)) % 500)
    jitter = (os.urandom(1)[0] / 255.0 - 0.5) * 2
    price = round(base + jitter, 2)
    return {
        'symbol': symbol,
        'name': symbol,
        'price': price,
        'changePercent': round(jitter, 2),
        'updated': datetime.now().isoformat(),
        'source': 'demo'
    }


def _normalize_symbol_for_yf(symbol: str) -> str:
    return symbol.replace('.', '-')


def _watchlist_quote(symbol: str):
    cached = _quote_from_cache(symbol)
    if cached:
        return cached
    data = _mock_quote(symbol)
    _store_quote_cache(symbol, data)
    return data


@app.route('/api/watchlist/quotes', methods=['GET'])
def get_watchlist_quotes():
    """Return batched cached mock quotes for watchlist."""
    try:
        symbols_param = request.args.get('symbols')
        if symbols_param:
            symbols = [s.upper().strip() for s in symbols_param.split(',') if s.strip()]
        else:
            symbols = WATCHLIST[:]
        quotes = [_watchlist_quote(sym) for sym in dict.fromkeys(symbols)]
        return jsonify({'success': True, 'data': quotes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/', methods=['GET'])
def index():
    """Root info route — the backend is API-only; this just confirms it's up."""
    return jsonify({
        'service': 'TradePerformanceVisualizer API',
        'status': 'running',
        'note': 'This is an API-only backend. Use the frontends, not this URL directly.',
        'endpoints': ['/api/health', '/api/explorer/data', '/api/db/accounts']
    })


@app.route('/api/health', methods=['GET'])
def health_check():
    """Check database connectivity as well as the HTTP service."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        app.logger.exception("Database health check failed")
        return jsonify({'status': 'unhealthy', 'database': 'unavailable'}), 503
    return jsonify({
        'status': 'healthy',
        'database': 'connected',
        'timestamp': datetime.now().isoformat()
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/explorer/data
# Returns the entire multi-user export as JSON.
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/explorer/data", methods=["GET"])
@require_basic_auth
def explorer_get_data():
    data = _load_full_export()
    if not data:
        return jsonify({
            "success": False,
            "error": "No export file found. Run snaptrade_export_all.py first, "
                     "or hit POST /api/explorer/refresh."
        }), 404

    return jsonify({"success": True, "data": data})


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/explorer/refresh
# Kicks off snaptrade_export_all.py in a background thread.
# ─────────────────────────────────────────────────────────────────────────────
_refresh_lock = threading.Lock()
_refresh_state = {"status": "idle", "error": None}


@app.route("/api/explorer/refresh", methods=["GET", "POST"])
@require_basic_auth
def explorer_refresh():
    if request.method == "GET":
        return jsonify({"success": True, **_refresh_state})
    if not _refresh_lock.acquire(blocking=False):
        return jsonify({"success": False, "error": "Refresh already running"}), 409
    _refresh_state.update(status="running", error=None)

    def _run():
        try:
            export_script = os.path.join(DATABASE_DIR, 'snaptrade_export_all.py')
            subprocess.run([sys.executable, export_script], timeout=120, check=True, cwd=BASE_DIR)
            with app.app_context():
                sync_export_to_db(export_file=os.path.join(ACCOUNT_DATA_DIR, "snaptrade_full_export.json"))
            _refresh_state.update(status="complete", error=None)
        except Exception:
            app.logger.exception("Explorer refresh failed")
            _refresh_state.update(status="failed", error="Export or database sync failed; check backend logs.")
        finally:
            _refresh_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "status": "running"}), 202

# ─────────────────────────────────────────────────────────────────────────────
# GET /api/explorer/meta
# Quick metadata: last exported time + list of usernames.
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/explorer/meta", methods=["GET"])
@require_basic_auth
def explorer_meta():
    data = _load_full_export()
    if not data:
        return jsonify({"success": True, "data": {"exported_at": None, "users": []}})

    users_meta = [
        {
            "username": u.get("username"),
            "exported_at": u.get("exported_at"),
            "account_count": len(u.get("accounts", [])),
        }
        for u in data.get("users", [])
    ]
    return jsonify({
        "success": True,
        "data": {
            "exported_at": data.get("exported_at"),
            "total_users": data.get("total_users", len(users_meta)),
            "users": users_meta,
        },
    })
# ── PostgreSQL table endpoints ─────────────────────────────────────────────

@app.route("/api/db/positions", methods=["GET"])
@require_basic_auth
def db_positions():
    rows = (
        db.session.query(Position, Account)
        .join(Account, Position.account_id == Account.id)
        .all()
    )
    return jsonify({"success": True, "data": [
        {
            "username": a.username,
            "account": a.name,
            "symbol": p.symbol,
            "description": p.description,
            "exchange": p.exchange,
            "asset_type": p.asset_type,
            "quantity": p.units,
            "price": p.price,
            "avg_cost": p.average_purchase_price,
            "market_value": p.market_value,
            "open_pnl": p.open_pnl,
        }
        for p, a in rows
    ]})

@app.route("/api/db/orders", methods=["GET"])
@require_basic_auth
def db_orders():
    rows = (
        db.session.query(Order, Account)
        .join(Account, Order.account_id == Account.id)
        .all()
    )
    return jsonify({"success": True, "data": [
        {
            "username": a.username,
            "account": a.name,
            "symbol": o.underlying_symbol,
            "side": o.action,
            "order_type": o.order_type,
            "quantity": o.total_quantity,
            "filled_quantity": o.filled_quantity,
            "price": o.execution_price,
            "status": o.status,
            "time_in_force": o.time_in_force,
            "created_at": o.time_placed.isoformat() if o.time_placed else None,
            "filled_at": o.time_executed.isoformat() if o.time_executed else None,
        }
        for o, a in rows
    ]})

@app.route("/api/db/activities", methods=["GET"])
@require_basic_auth
def db_activities():
    rows = (
        db.session.query(Activity, Account)
        .join(Account, Activity.account_id == Account.id)
        .all()
    )
    return jsonify({"success": True, "data": [
        {
            "username": a.username,
            "account": a.name,
            "trade_date": act.trade_date.isoformat() if act.trade_date else None,
            "type": act.type,
            "symbol": act.symbol,
            "description": act.description,
            "amount": act.amount,
            "units": act.units,
            "price": act.price,
            "fee": act.fee,
        }
        for act, a in rows
    ]})

@app.route("/api/db/accounts", methods=["GET"])
@require_basic_auth
def db_accounts():
    rows = (
        db.session.query(Account)
        .all()
    )
    return jsonify({"success": True, "data": [
        {
            "username": a.username,
            "account_id": a.id,
            "name": a.name,
            "number": a.number,
            "institution": a.institution_name,
            "type": a.account_type,
            "status": a.status,
            "is_paper": a.is_paper,
            "currency": a.currency,
            "cash": a.cash,
            "buying_power": a.buying_power,
        }
        for a in rows
    ]})

@app.route("/api/db/sync", methods=["POST"])
@require_basic_auth
def db_sync_endpoint():
    """Manually trigger a DB sync from the existing JSON export."""
    try:
        stats = sync_export_to_db(export_file=os.path.join(ACCOUNT_DATA_DIR, "snaptrade_full_export.json"), verbose=True)
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
        
if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=5001)


