import os
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine
from dash import Dash, dash_table, html, dcc, Input, Output

# ── DB Connection ──────────────────────────
engine = create_engine(
    os.environ.get("DATABASE_URL", f"sqlite:///{Path(__file__).with_name('portfolio.db')}")
)

# ── Load Data ──────────────────────────────
df_accounts = pd.read_sql("SELECT * FROM accounts", engine)
df_transactions = pd.read_sql("SELECT * FROM transactions", engine)
df_positions = pd.read_sql("SELECT * FROM positions", engine)

# ── Clean Data ─────────────────────────────
df_transactions = df_transactions.drop(columns=["raw_json"], errors="ignore").fillna("")
df_accounts = df_accounts.fillna("")
df_positions = df_positions.fillna("")

# ── Get users ──────────────────────────────
users = sorted(df_accounts["username"].dropna().unique())
user_options = [{"label": "All Users", "value": "ALL"}] + [
    {"label": u, "value": u} for u in users
]

# ── Reusable Table ─────────────────────────
def styled_table(table_id):
    return dash_table.DataTable(
        id=table_id,

        page_size=10,
        filter_action="native",
        sort_action="native",

        style_table={
            'height': '320px',
            'overflowY': 'auto',
            'overflowX': 'auto',
            'border': '1px solid #ddd',
            'borderRadius': '6px',
        },

        style_cell={
            'textAlign': 'left',
            'padding': '8px',
            'fontSize': '13px',
            'whiteSpace': 'nowrap',
            'overflow': 'hidden',
            'textOverflow': 'ellipsis',
            'maxWidth': '180px',
        },

        style_header={
            'backgroundColor': '#f8f9fa',
            'fontWeight': 'bold',
            'position': 'sticky',
            'top': 0,
            'zIndex': 1,
        },

        style_data_conditional=[
            {'if': {'row_index': 'odd'}, 'backgroundColor': '#fafafa'},
            {'if': {'state': 'active'}, 'backgroundColor': '#e6f2ff'},
        ],
    )

# ── App ────────────────────────────────────
app = Dash(__name__)

app.layout = html.Div([

    html.Div([

        html.H1("Portfolio Dashboard"),

        # User selector
        html.Label("Select User:", style={"marginTop": "10px"}),
        dcc.Dropdown(
            id="user-dropdown",
            options=user_options,
            value="ALL",
            clearable=False,
            style={"width": "300px", "marginBottom": "20px"}
        ),

        # ── Accounts ───────────────────────
        html.H2("Accounts"),
        styled_table("accounts_table"),

        # ── Transactions ──────────────────
        html.H2("Transactions", style={"marginTop": "40px"}),
        styled_table("transactions_table"),

        # ── Positions ─────────────────────
        html.H2("Positions", style={"marginTop": "40px"}),
        styled_table("positions_table"),

    ], style={
        "width": "95%",
        "margin": "auto",
        "fontFamily": "Arial, sans-serif"
    })

])

# ── Callback ───────────────────────────────
@app.callback(
    Output("accounts_table", "data"),
    Output("accounts_table", "columns"),
    Output("transactions_table", "data"),
    Output("transactions_table", "columns"),
    Output("positions_table", "data"),
    Output("positions_table", "columns"),
    Input("user-dropdown", "value")
)
def filter_data(selected_user):

    if selected_user == "ALL":
        acc = df_accounts
    else:
        acc = df_accounts[df_accounts["username"] == selected_user]

    # Filter related tables
    account_ids = acc["id"].tolist()

    txn = df_transactions[df_transactions["account_id"].isin(account_ids)]
    pos = df_positions[df_positions["account_id"].isin(account_ids)]

    return (
        acc.to_dict("records"),
        [{"name": i, "id": i} for i in acc.columns],

        txn.to_dict("records"),
        [{"name": i, "id": i} for i in txn.columns],

        pos.to_dict("records"),
        [{"name": i, "id": i} for i in pos.columns],
    )

# ── Run ────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)