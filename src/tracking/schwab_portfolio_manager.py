"""
Schwab Portfolio & Individual Positions Manager.
Stores accounts, positions (Equities, Options, Mutual Funds), and historical portfolio snapshots
in a dedicated SQLite database (data/schwab_portfolio.db).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config

logger = logging.getLogger("schwab_portfolio")

DB_PATH = config.BASE_DIR / "data" / "schwab_portfolio.db"
_db_lock = threading.Lock()


def get_db_connection() -> sqlite3.Connection:
    """Return a connection to the dedicated Schwab portfolio SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_portfolio_db() -> None:
    """Initialize schema in data/schwab_portfolio.db."""
    with _db_lock:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS schwab_accounts (
                    account_id TEXT PRIMARY KEY,
                    account_number_masked TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    liquidation_value REAL DEFAULT 0.0,
                    cash_balance REAL DEFAULT 0.0,
                    available_funds REAL DEFAULT 0.0,
                    buying_power REAL DEFAULT 0.0,
                    day_profit_loss REAL DEFAULT 0.0,
                    day_profit_loss_pct REAL DEFAULT 0.0,
                    long_market_value REAL DEFAULT 0.0,
                    mutual_fund_value REAL DEFAULT 0.0,
                    option_market_value REAL DEFAULT 0.0,
                    last_synced TEXT NOT NULL
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS schwab_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    account_number_masked TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    underlying_symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    quantity REAL NOT NULL,
                    current_price REAL DEFAULT 0.0,
                    average_price REAL DEFAULT 0.0,
                    market_value REAL DEFAULT 0.0,
                    cost_basis REAL DEFAULT 0.0,
                    unrealized_profit_loss REAL DEFAULT 0.0,
                    unrealized_profit_loss_pct REAL DEFAULT 0.0,
                    day_profit_loss REAL DEFAULT 0.0,
                    day_profit_loss_pct REAL DEFAULT 0.0,
                    option_strike REAL,
                    option_type TEXT,
                    option_expiration TEXT,
                    last_synced TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES schwab_accounts(account_id)
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    date_str TEXT NOT NULL,
                    total_liquidation_value REAL DEFAULT 0.0,
                    total_cash_balance REAL DEFAULT 0.0,
                    total_day_pnl REAL DEFAULT 0.0,
                    total_day_pnl_pct REAL DEFAULT 0.0,
                    total_unrealized_pnl REAL DEFAULT 0.0,
                    total_unrealized_pnl_pct REAL DEFAULT 0.0,
                    equity_value REAL DEFAULT 0.0,
                    option_value REAL DEFAULT 0.0,
                    mutual_fund_value REAL DEFAULT 0.0,
                    position_count INTEGER DEFAULT 0
                )
            """)

            c.execute("CREATE INDEX IF NOT EXISTS idx_positions_account ON schwab_positions(account_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON schwab_positions(symbol)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_positions_underlying ON schwab_positions(underlying_symbol)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_positions_asset_type ON schwab_positions(asset_type)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_date ON portfolio_snapshots(date_str)")
            conn.commit()


def mask_account_number(acct_num: Any) -> str:
    """Mask account number for privacy (e.g. 123456929 -> ***6929)."""
    s = str(acct_num or "").strip()
    if len(s) >= 4:
        return f"***{s[-4:]}"
    return s or "Unknown"


def parse_option_details(symbol: str, description: str, put_call: Optional[str] = None) -> Dict[str, Any]:
    """
    Parse strike, option type (CALL/PUT), and expiration from OCC symbol or description.
    Example OCC symbol: NVO   270115C00070000 -> Exp: 2027-01-15, Type: CALL, Strike: 70.0
    Example description: 'NOVO-NORDISK A S 01/15/2027 $70 Call'
    """
    res = {
        "strike": None,
        "option_type": put_call.upper() if put_call else None,
        "expiration": None,
    }

    # Method 1: OCC Symbol Parse (e.g., SYMBOL + YYMMDD + C/P + 8 digits)
    occ_match = re.search(r'([A-Z]+)\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})', symbol.upper())
    if occ_match:
        yy, mm, dd, cp, strike_str = occ_match.group(2), occ_match.group(3), occ_match.group(4), occ_match.group(5), occ_match.group(6)
        res["expiration"] = f"20{yy}-{mm}-{dd}"
        res["option_type"] = "CALL" if cp == "C" else "PUT"
        try:
            res["strike"] = round(float(strike_str) / 1000.0, 2)
        except Exception:
            pass
        return res

    # Method 2: Description Regex Parse
    if description:
        exp_match = re.search(r'(\d{2})/(\d{2})/(\d{4})', description)
        if exp_match:
            mm, dd, yyyy = exp_match.group(1), exp_match.group(2), exp_match.group(3)
            res["expiration"] = f"{yyyy}-{mm}-{dd}"

        strike_match = re.search(r'\$([0-9.]+)', description)
        if strike_match:
            try:
                res["strike"] = float(strike_match.group(1))
            except Exception:
                pass

        if "CALL" in description.upper():
            res["option_type"] = "CALL"
        elif "PUT" in description.upper():
            res["option_type"] = "PUT"

    return res


def sync_schwab_positions(client=None) -> Dict[str, Any]:
    """
    Fetch live positions and account balances from Schwab API across all accounts
    and persist transactionally into data/schwab_portfolio.db.
    """
    init_portfolio_db()

    if client is None:
        from src.clients.schwab_client import get_schwab_client
        client = get_schwab_client()

    logger.info("🔄 [SchwabPortfolio] Fetching live positions and balances from Schwab API...")
    resp = client.get_accounts(fields=[client.Account.Fields.POSITIONS])
    if resp.status_code != 200:
        err = f"Schwab API returned HTTP {resp.status_code}: {resp.text}"
        logger.error(f"❌ [SchwabPortfolio] {err}")
        return {"success": False, "error": err}

    accounts_data = resp.json()
    if not isinstance(accounts_data, list):
        return {"success": False, "error": "Invalid API response structure"}

    now_iso = datetime.now(timezone.utc).isoformat()
    now_mt = datetime.now(ZoneInfo("America/Denver"))
    date_str = now_mt.strftime("%Y-%m-%d")

    total_portfolio_liq = 0.0
    total_cash_bal = 0.0
    total_day_pnl = 0.0
    total_unrealized_pnl = 0.0
    total_cost_basis = 0.0
    total_equity_val = 0.0
    total_option_val = 0.0
    total_fund_val = 0.0
    all_positions: List[Dict[str, Any]] = []

    with _db_lock:
        with get_db_connection() as conn:
            c = conn.cursor()

            # Clear previous active accounts & positions tables (refresh with live snapshot)
            c.execute("DELETE FROM schwab_positions")
            c.execute("DELETE FROM schwab_accounts")

            for idx, item in enumerate(accounts_data, 1):
                sa = item.get("securitiesAccount") or {}
                raw_num = str(sa.get("accountNumber") or f"ACCT_{idx}")
                masked_num = mask_account_number(raw_num)
                acct_type = str(sa.get("type") or "MARGIN").upper()
                display_name = f"{acct_type.capitalize()} ({masked_num})"

                curr_bal = sa.get("currentBalances") or {}

                liq_val = float(curr_bal.get("liquidationValue") or 0.0)
                cash_bal = float(curr_bal.get("cashBalance") or curr_bal.get("moneyMarketFund") or 0.0)
                avail_funds = float(curr_bal.get("availableFunds") or curr_bal.get("buyingPower") or 0.0)
                buying_power = float(curr_bal.get("buyingPower") or 0.0)
                long_mkt = float(curr_bal.get("longMarketValue") or 0.0)
                mutual_mkt = float(curr_bal.get("mutualFundValue") or 0.0)
                long_opt = float(curr_bal.get("longOptionMarketValue") or 0.0)
                short_opt = float(curr_bal.get("shortOptionMarketValue") or 0.0)
                opt_val = long_opt + short_opt

                # Calculate Day P/L for account
                raw_positions = sa.get("positions") or []
                acct_day_pnl = sum(float(p.get("currentDayProfitLoss") or 0.0) for p in raw_positions)
                acct_day_pnl_pct = round((acct_day_pnl / (liq_val - acct_day_pnl) * 100.0), 2) if (liq_val - acct_day_pnl) > 0 else 0.0

                # Upsert account
                c.execute("""
                    INSERT OR REPLACE INTO schwab_accounts (
                        account_id, account_number_masked, account_type, display_name,
                        liquidation_value, cash_balance, available_funds, buying_power,
                        day_profit_loss, day_profit_loss_pct, long_market_value,
                        mutual_fund_value, option_market_value, last_synced
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    raw_num, masked_num, acct_type, display_name,
                    liq_val, cash_bal, avail_funds, buying_power,
                    acct_day_pnl, acct_day_pnl_pct, long_mkt,
                    mutual_mkt, opt_val, now_iso
                ))

                total_portfolio_liq += liq_val
                total_cash_bal += cash_bal
                total_equity_val += long_mkt
                total_fund_val += mutual_mkt
                total_option_val += opt_val
                total_day_pnl += acct_day_pnl

                # Process positions
                for p in raw_positions:
                    inst = p.get("instrument") or {}
                    raw_sym = str(inst.get("symbol") or "").strip().upper()
                    underlying = str(inst.get("underlyingSymbol") or raw_sym).strip().upper()
                    asset_type = str(inst.get("assetType") or "EQUITY").upper()
                    desc = str(inst.get("description") or "")
                    
                    # Quantity (long - short)
                    long_qty = float(p.get("longQuantity") or 0.0)
                    short_qty = float(p.get("shortQuantity") or 0.0)
                    qty = long_qty if long_qty > 0 else (-short_qty if short_qty > 0 else 0.0)
                    if qty == 0.0:
                        continue

                    avg_price = float(p.get("averagePrice") or p.get("averageLongPrice") or 0.0)
                    mkt_val = float(p.get("marketValue") or 0.0)
                    
                    # Current price per unit
                    curr_px = round(mkt_val / qty, 2) if qty != 0 else avg_price
                    if asset_type == "OPTION":
                        curr_px = round(mkt_val / (qty * 100.0), 2) if qty != 0 else avg_price

                    day_gl = float(p.get("currentDayProfitLoss") or 0.0)
                    day_gl_pct = float(p.get("currentDayProfitLossPercentage") or 0.0)

                    # Total open unrealized P/L
                    unrealized_gl = float(p.get("longOpenProfitLoss") or 0.0)
                    cost_basis = round(mkt_val - unrealized_gl, 2)
                    if cost_basis <= 0 and avg_price > 0:
                        cost_basis = round(avg_price * qty * (100.0 if asset_type == "OPTION" else 1.0), 2)
                        unrealized_gl = round(mkt_val - cost_basis, 2)
                    
                    unrealized_gl_pct = round((unrealized_gl / cost_basis * 100.0), 2) if cost_basis > 0 else 0.0

                    total_unrealized_pnl += unrealized_gl
                    total_cost_basis += cost_basis

                    # Parse option specifics
                    opt_details = {"strike": None, "option_type": None, "expiration": None}
                    if asset_type == "OPTION":
                        opt_details = parse_option_details(raw_sym, desc, inst.get("putCall"))
                        if not underlying or underlying == raw_sym:
                            underlying = raw_sym.split()[0]

                    c.execute("""
                        INSERT INTO schwab_positions (
                            account_id, account_number_masked, account_type, symbol,
                            underlying_symbol, asset_type, description, quantity,
                            current_price, average_price, market_value, cost_basis,
                            unrealized_profit_loss, unrealized_profit_loss_pct,
                            day_profit_loss, day_profit_loss_pct, option_strike,
                            option_type, option_expiration, last_synced
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        raw_num, masked_num, acct_type, raw_sym,
                        underlying, asset_type, desc, qty,
                        curr_px, avg_price, mkt_val, cost_basis,
                        unrealized_gl, unrealized_gl_pct, day_gl,
                        day_gl_pct, opt_details["strike"], opt_details["option_type"],
                        opt_details["expiration"], now_iso
                    ))

                    all_positions.append({
                        "account_id": raw_num,
                        "account_masked": masked_num,
                        "account_type": acct_type,
                        "symbol": raw_sym,
                        "underlying": underlying,
                        "asset_type": asset_type,
                        "quantity": qty,
                        "market_value": mkt_val,
                        "day_profit_loss": day_gl,
                    })

            # Record snapshot
            total_day_pct = round((total_day_pnl / (total_portfolio_liq - total_day_pnl) * 100.0), 2) if (total_portfolio_liq - total_day_pnl) > 0 else 0.0
            total_unrealized_pct = round((total_unrealized_pnl / total_cost_basis * 100.0), 2) if total_cost_basis > 0 else 0.0

            c.execute("""
                INSERT INTO portfolio_snapshots (
                    timestamp, date_str, total_liquidation_value, total_cash_balance,
                    total_day_pnl, total_day_pnl_pct, total_unrealized_pnl,
                    total_unrealized_pnl_pct, equity_value, option_value,
                    mutual_fund_value, position_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_iso, date_str, total_portfolio_liq, total_cash_bal,
                total_day_pnl, total_day_pct, total_unrealized_pnl,
                total_unrealized_pct, total_equity_val, total_option_val,
                total_fund_val, len(all_positions)
            ))

            conn.commit()

    logger.info(
        f"✅ [SchwabPortfolio] Synced {len(accounts_data)} accounts, {len(all_positions)} positions. "
        f"Total Value: ${total_portfolio_liq:,.2f} | Day P/L: ${total_day_pnl:+,.2f} ({total_day_pct:+.2f}%)"
    )

    return {
        "success": True,
        "accounts_count": len(accounts_data),
        "positions_count": len(all_positions),
        "total_liquidation_value": total_portfolio_liq,
        "total_cash_balance": total_cash_bal,
        "total_day_pnl": total_day_pnl,
        "total_day_pnl_pct": total_day_pct,
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_unrealized_pnl_pct": total_unrealized_pct,
        "last_synced": now_iso,
    }


def get_portfolio_summary() -> Dict[str, Any]:
    """Retrieve aggregate summary cards and account breakdown from data/schwab_portfolio.db."""
    init_portfolio_db()
    with _db_lock:
        with get_db_connection() as conn:
            c = conn.cursor()
            accounts = c.execute("SELECT * FROM schwab_accounts ORDER BY liquidation_value DESC").fetchall()
            
            latest_snap = c.execute("""
                SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT 1
            """).fetchone()

            pos_counts = c.execute("""
                SELECT asset_type, COUNT(*) as cnt, SUM(market_value) as val 
                FROM schwab_positions 
                GROUP BY asset_type
            """).fetchall()

    accts_list = [dict(a) for a in accounts]
    snap_dict = dict(latest_snap) if latest_snap else {}

    asset_breakdown = {}
    for p in pos_counts:
        asset_breakdown[p["asset_type"]] = {
            "count": p["cnt"],
            "market_value": round(float(p["val"] or 0.0), 2),
        }

    return {
        "total_liquidation_value": snap_dict.get("total_liquidation_value", 0.0),
        "total_cash_balance": snap_dict.get("total_cash_balance", 0.0),
        "total_day_pnl": snap_dict.get("total_day_pnl", 0.0),
        "total_day_pnl_pct": snap_dict.get("total_day_pnl_pct", 0.0),
        "total_unrealized_pnl": snap_dict.get("total_unrealized_pnl", 0.0),
        "total_unrealized_pnl_pct": snap_dict.get("total_unrealized_pnl_pct", 0.0),
        "equity_value": snap_dict.get("equity_value", 0.0),
        "option_value": snap_dict.get("option_value", 0.0),
        "mutual_fund_value": snap_dict.get("mutual_fund_value", 0.0),
        "position_count": snap_dict.get("position_count", 0),
        "last_synced": snap_dict.get("timestamp"),
        "accounts": accts_list,
        "asset_breakdown": asset_breakdown,
    }


def get_portfolio_positions(
    account_id: Optional[str] = None,
    asset_type: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query positions with filtering, sorting, and metadata enrichment."""
    init_portfolio_db()
    with _db_lock:
        with get_db_connection() as conn:
            c = conn.cursor()
            query = "SELECT * FROM schwab_positions WHERE 1=1"
            params = []

            if account_id and account_id.strip() and account_id.upper() != "ALL":
                query += " AND (account_id = ? OR account_number_masked = ?)"
                params.extend([account_id.strip(), account_id.strip()])

            if asset_type and asset_type.strip() and asset_type.upper() != "ALL":
                query += " AND asset_type = ?"
                params.append(asset_type.strip().upper())

            if search and search.strip():
                s = f"%{search.strip().upper()}%"
                query += " AND (symbol LIKE ? OR underlying_symbol LIKE ? OR description LIKE ?)"
                params.extend([s, s, s])

            # Sorting
            sort_sql = "market_value DESC"
            if sort_by == "day_pnl_desc":
                sort_sql = "day_profit_loss DESC"
            elif sort_by == "day_pnl_asc":
                sort_sql = "day_profit_loss ASC"
            elif sort_by == "unrealized_pnl_desc":
                sort_sql = "unrealized_profit_loss DESC"
            elif sort_by == "unrealized_pnl_asc":
                sort_sql = "unrealized_profit_loss ASC"
            elif sort_by == "symbol":
                sort_sql = "underlying_symbol ASC, symbol ASC"
            elif sort_by == "asset_type":
                sort_sql = "asset_type ASC, market_value DESC"

            query += f" ORDER BY {sort_sql}"
            rows = c.execute(query, params).fetchall()

    return [dict(r) for r in rows]
