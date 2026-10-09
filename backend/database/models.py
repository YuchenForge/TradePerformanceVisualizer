"""
models.py — SQLAlchemy ORM models for SnapTrade account data.
Database: SQLite (portfolio.db)
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import String, Float, Boolean, Integer, DateTime, ForeignKey, Text, JSON
db = SQLAlchemy()


# ── Account ──────────────────────────────────────────────────────────────────

class Account(db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.String, primary_key=True)        # SnapTrade account UUID
    username = db.Column(db.String, nullable=False)       # local app username
    name = db.Column(db.String)                           # e.g. "Alpaca Paper"
    number = db.Column(db.String)                         # brokerage account number
    institution_name = db.Column(db.String)
    account_type = db.Column(db.String)                   # meta.type  e.g. "Margin"
    status = db.Column(db.String)                         # "open" / "closed"
    is_paper = db.Column(Boolean, default=False)
    currency = db.Column(db.String, default="USD")
    total_balance = db.Column(Float, default=0.0)
    cash = db.Column(Float, default=0.0)
    buying_power = db.Column(Float, default=0.0)
    created_date = db.Column(DateTime, nullable=True)
    last_synced = db.Column(DateTime, default=datetime.utcnow)

    # relationships
    positions    = db.relationship("Position",    back_populates="account", cascade="all, delete-orphan")
    transactions = db.relationship("Activity", back_populates="account", cascade="all, delete-orphan")
    orders       = db.relationship("Order",       back_populates="account", cascade="all, delete-orphan")
    performance  = db.relationship("Performance", back_populates="account", cascade="all, delete-orphan", uselist=False)
    holdings     = db.relationship("Holding",     back_populates="account", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Account {self.name} ({self.id[:8]}…)>"


# ── Position ──────────────────────────────────────────────────────────────────

class Position(db.Model):
    __tablename__ = "positions"

    id = db.Column(Integer, primary_key=True, autoincrement=True)
    account_id = db.Column(String, ForeignKey("accounts.id"), nullable=False)

    symbol = db.Column(db.String, nullable=False)          # e.g. "AAPL"
    description = db.Column(db.String)                     # e.g. "Apple Inc"
    exchange = db.Column(db.String)                        # e.g. "NASDAQ"
    asset_type = db.Column(db.String)                      # "cs" / "et" / …
    currency = db.Column(db.String, default="USD")

    units = db.Column(Float, default=0.0)               # shares held
    price = db.Column(Float, default=0.0)               # current price
    average_purchase_price = db.Column(Float, default=0.0)
    open_pnl = db.Column(Float, default=0.0)
    market_value = db.Column(Float, default=0.0)        # units × price
    cost_basis = db.Column(Float, default=0.0)          # units × avg_price
    is_cash_equivalent = db.Column(Boolean, default=False)

    logo_url = db.Column(String, nullable=True)
    figi_code = db.Column(String, nullable=True)

    fetched_at = db.Column(DateTime, default=datetime.utcnow)

    account = db.relationship("Account", back_populates="positions")

    def __repr__(self):
        return f"<Position {self.symbol} × {self.units}>"
# ── User ───────────────────────────────────────────────────────────────

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, unique=True, nullable=False)
    snaptrade_user_id = db.Column(db.String)
    exported_at = db.Column(db.DateTime)
# ── Transaction ───────────────────────────────────────────────────────────────

class Activity(db.Model):
    __tablename__ = "transactions"

    id = db.Column(String, primary_key=True)            # SnapTrade activity UUID
    account_id = db.Column(String, ForeignKey("accounts.id"), nullable=False)

    symbol = db.Column(String, nullable=True)
    option_ticker = db.Column(String, nullable=True)    # e.g. "AAPL  260304C00190000"
    type = db.Column(String)                            # BUY / SELL / FEE / JNLC …
    action = db.Column(String)                          # parsed: "BUY FILL" etc.
    description =  db.Column(Text)
    price_from_description =  db.Column(String, nullable=True)  # extracted fill price

    amount =  db.Column(Float, default=0.0)
    price =  db.Column(Float, default=0.0)
    units =  db.Column(Float, default=0.0)
    fee =  db.Column(Float, default=0.0)
    currency =  db.Column(String, default="USD")

    trade_date =  db.Column(DateTime, nullable=True)
    settlement_date =  db.Column(DateTime, nullable=True)
    institution =  db.Column(String, nullable=True)

    account = db.relationship("Account", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction {self.type} {self.symbol} ${self.amount}>"


# ── Order ─────────────────────────────────────────────────────────────────────

class Order(db.Model):
    __tablename__ = "orders"

    id =  db.Column(String, primary_key=True)            # brokerage_order_id
    account_id =  db.Column(String, ForeignKey("accounts.id"), nullable=False)

    status =  db.Column(String)                          # EXECUTED / EXPIRED / …
    action =  db.Column(String)                          # BUY / SELL
    order_type =  db.Column(String)                      # Limit / Market
    time_in_force =  db.Column(String)

    # underlying symbol (for options this is the underlying, e.g. SPY)
    underlying_symbol =  db.Column(String, nullable=True)
    # option-specific fields
    option_ticker =  db.Column(String, nullable=True)    # e.g. "SPY   260303C00500000"
    option_type =  db.Column(String, nullable=True)      # CALL / PUT
    strike_price =  db.Column(Float, nullable=True)
    expiration_date =  db.Column(String, nullable=True)

    total_quantity =  db.Column(Float, default=0.0)
    filled_quantity =  db.Column(Float, default=0.0)
    canceled_quantity =  db.Column(Float, default=0.0)
    open_quantity =  db.Column(Float, default=0.0)

    limit_price =  db.Column(Float, nullable=True)
    stop_price =  db.Column(Float, nullable=True)
    execution_price =  db.Column(Float, nullable=True)

    time_placed =  db.Column(DateTime, nullable=True)
    time_executed =  db.Column(DateTime, nullable=True)
    time_updated =  db.Column(DateTime, nullable=True)
    expiry_date =  db.Column(DateTime, nullable=True)

    account = db.relationship("Account", back_populates="orders")

    def __repr__(self):
        return f"<Order {self.action} {self.underlying_symbol} status={self.status}>"


# ── Performance ───────────────────────────────────────────────────────────────

class Performance(db.Model):
    """
    One row per account. Stores the raw performance blob from
    get_user_account_performance as JSON plus key scalar extracts.
    """
    __tablename__ = "performance"

    id =  db.Column(Integer, primary_key=True, autoincrement=True)
    account_id =  db.Column(String, ForeignKey("accounts.id"), nullable=False, unique=True)

    total_equity_timeframe =  db.Column(Float, nullable=True)
    net_cumulative_return =  db.Column(Float, nullable=True)
    annualized_return =  db.Column(Float, nullable=True)
    max_drawdown =  db.Column(Float, nullable=True)
    sharpe_ratio =  db.Column(Float, nullable=True)
    volatility =  db.Column(Float, nullable=True)
    beta =  db.Column(Float, nullable=True)

    raw_data =  db.Column(JSON, nullable=True)   # full blob for anything not extracted
    fetched_at =  db.Column(DateTime, default=datetime.utcnow)

    account = db.relationship("Account", back_populates="performance")

    def __repr__(self):
        return f"<Performance account={self.account_id[:8]}…>"


# ── Holding (from get_user_account_holdings) ──────────────────────────────────

class Holding(db.Model):
    """
    Holdings response is richer than positions — it includes cost basis,
    open/closed lots, and currency detail. Stored alongside positions.
    """
    __tablename__ = "holdings"

    id =  db.Column(Integer, primary_key=True, autoincrement=True)
    account_id =  db.Column(String, ForeignKey("accounts.id"), nullable=False)

    symbol =  db.Column(String, nullable=False)
    description =  db.Column(String, nullable=True)
    units =  db.Column(Float, default=0.0)
    price =  db.Column(Float, default=0.0)
    average_purchase_price =  db.Column(Float, default=0.0)
    market_value =  db.Column(Float, default=0.0)
    cost_basis =  db.Column(Float, default=0.0)
    open_pnl =  db.Column(Float, default=0.0)
    currency =  db.Column(String, default="USD")

    raw_data =  db.Column(JSON, nullable=True)   # full holding object
    fetched_at =  db.Column(DateTime, default=datetime.utcnow)

    account = db.relationship("Account", back_populates="holdings")

    def __repr__(self):
        return f"<Holding {self.symbol} × {self.units}>"
