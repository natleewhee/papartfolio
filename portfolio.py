from fetcher import fetch_fx_rate, get_prices_bulk
from portfolio_db import (
    get_all_holdings, save_daily_snapshot, save_portfolio_aggregate,
    get_earliest_aggregate_since,
)
from config import HOME_CURRENCY, TIMEZONE
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

def _today():
    """SGT-aware 'today', so snapshot dates stay correct regardless of the server's own timezone (Render runs UTC)."""
    return datetime.now(pytz.timezone(TIMEZONE))

CURRENCY_SYMBOLS = {"USD": "$", "SGD": "S$"}

def format_shares(shares):
    """Render a share count without a spurious trailing '.0' for the common
    whole-share case, while still showing full precision for fractional
    shares (e.g. IBKR DRIP/fractional positions)."""
    if shares == int(shares):
        return str(int(shares))
    return f"{shares:.4f}".rstrip("0").rstrip(".")

def fmt_money(value, currency="USD", privacy=False, show_sign=False, decimals=2):
    """Format an amount in its own currency (sign before the symbol, e.g. -S$38.00),
    or mask it entirely when privacy mode is on."""
    if privacy:
        return "•••"
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    sign = ""
    if show_sign:
        sign = "+" if value >= 0 else "-"
        value = abs(value)
    return f"{sign}{symbol}{value:,.{decimals}f}"

def format_holdings_table(holdings, privacy=False):
    """Render holdings as an aligned monospace table — wrap the result in a
    Markdown code block (```) so Telegram renders it with a fixed-width font.

    Columns read left to right as: identity, today's per-share price, what you
    originally put in (cost basis), what it's worth now, overall gain, then
    today's % move. (The $ amount for today's move is deliberately left out —
    %CHG alone conveys direction and size, and the combined row was wide
    enough to overflow narrower phone screens, which wrap instead of
    scrolling a Telegram code block.)"""
    header = f"{'SYMBOL':<7}{'PRICE':>9}{'COST':>10}{'VALUE':>10}{'GAIN%':>8}{'%CHG':>8}"
    lines = [header, "-" * len(header)]
    for h in holdings:
        currency = h["currency"]
        price_str = fmt_money(h["current_price"], currency, privacy, decimals=2)
        cost_str = fmt_money(h["cost_basis"], currency, privacy, decimals=0)
        value_str = fmt_money(h["current_value"], currency, privacy, decimals=0)
        # Percentages stay visible in privacy mode — only $ amounts are
        # masked (matches /privacy's own documented behavior, and how /list
        # and the market-open/close pings already treat these same figures).
        gain_str = f"{h['unrealized_gain_pct']:+.1f}%"
        pct_str = f"{h['daily_change_%']:+.1f}%"
        lines.append(
            f"{h['symbol']:<7}{price_str:>9}{cost_str:>10}{value_str:>10}{gain_str:>8}{pct_str:>8}"
        )
    return "\n".join(lines)

def calculate_portfolio_metrics(save_snapshot=True):
    """
    Calculate portfolio value, P&L, and per-holding metrics.

    Each holding is priced and displayed in its own native currency (USD for
    most tickers, SGD for .SI/SGX tickers), but portfolio-level totals
    (total_value, daily_change_$, etc.) are converted to HOME_CURRENCY so
    mixed-currency holdings combine into one meaningful grand total.

    save_snapshot: record this calculation into daily_snapshots /
    portfolio_aggregates. Pass False for read-only checks (e.g. /list) so
    casual lookups don't overwrite the day's official snapshot.

    Returns:
    {
        "total_value": 50000.00,          # in HOME_CURRENCY
        "total_cost_basis": 48000.00,     # in HOME_CURRENCY
        "unrealized_gain": 2000.00,       # in HOME_CURRENCY
        "unrealized_gain_pct": 4.17,
        "daily_change_$": 150.00,         # in HOME_CURRENCY
        "daily_change_%": 0.3,
        "home_currency": "SGD",
        "fx_rates": {"USD": 1.348},       # non-home currencies actually used, and their rate to HOME_CURRENCY
        "fx_warnings": ["USD"],           # currencies where no live/cached FX rate was available (1:1 was assumed)
        "failed_symbols": ["XYZ"],
        "best_performer": {...} or None,
        "worst_performer": {...} or None,
        "holdings": [
            {
                "symbol": "ECHO",
                "currency": "USD",         # this holding's native currency
                "shares": 50,
                "avg_cost": 12.50,         # native currency
                "current_price": 15.00,    # native currency
                "current_value": 750.00,   # native currency
                "cost_basis": 625.00,      # native currency
                "unrealized_gain": 125.00, # native currency
                "unrealized_gain_pct": 20.0,
                "daily_change_$": 25.00,   # native currency
                "daily_change_%": 1.7,
                "pct_of_portfolio": 23.4,  # computed on HOME_CURRENCY basis
            }
        ]
    }
    """
    holdings = get_all_holdings()

    if not holdings:
        return {
            "total_value": 0,
            "total_cost_basis": 0,
            "unrealized_gain": 0,
            "unrealized_gain_pct": 0,
            "daily_change_$": 0,
            "daily_change_%": 0,
            "home_currency": HOME_CURRENCY,
            "fx_rates": {},
            "fx_warnings": [],
            "failed_symbols": [],
            "best_performer": None,
            "worst_performer": None,
            "holdings": [],
        }

    portfolio_data = []
    total_value = 0  # HOME_CURRENCY
    total_cost_basis = 0  # HOME_CURRENCY
    failed_symbols = []
    fx_rates = {}
    fx_warnings = []

    # Fetch every holding's price concurrently — each is a blocking network
    # call (Finnhub or yfinance, routed per symbol's market), so sequential
    # fetching scaled linearly with portfolio size for no reason.
    price_data_by_symbol = get_prices_bulk(h["symbol"] for h in holdings)

    for holding in holdings:
        symbol = holding["symbol"]
        shares = holding["shares"]
        avg_cost = holding["avg_cost"]
        currency = holding.get("currency") or "USD"

        price_data = price_data_by_symbol.get(symbol)
        if not price_data or not price_data["price"]:
            logger.warning(f"⚠️ Could not fetch price for {symbol}")
            failed_symbols.append(symbol)
            continue

        current_price = price_data["price"]
        current_value = current_price * shares  # native currency
        cost_basis = avg_cost * shares  # native currency

        unrealized_gain = current_value - cost_basis
        unrealized_gain_pct = (unrealized_gain / cost_basis * 100) if cost_basis > 0 else 0

        # Daily change (from price change), native currency
        daily_change_dollar = (price_data["change"] or 0) * shares
        daily_change_pct = price_data["change_pct"] or 0

        fx_rate = fetch_fx_rate(currency, HOME_CURRENCY)
        if fx_rate is None:
            fx_rate = 1.0
            if currency not in fx_warnings:
                fx_warnings.append(currency)
        if currency != HOME_CURRENCY:
            fx_rates[currency] = fx_rate

        portfolio_data.append({
            "symbol": symbol,
            "currency": currency,
            "shares": shares,
            "avg_cost": round(avg_cost, 2),
            "current_price": round(current_price, 2),
            "current_value": round(current_value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_gain": round(unrealized_gain, 2),
            "unrealized_gain_pct": round(unrealized_gain_pct, 2),
            "daily_change_$": round(daily_change_dollar, 2),
            "daily_change_%": round(daily_change_pct, 2),
            "_current_value_home": current_value * fx_rate,
            "_daily_change_home": daily_change_dollar * fx_rate,
        })

        total_value += current_value * fx_rate
        total_cost_basis += cost_basis * fx_rate

        if save_snapshot:
            save_daily_snapshot(
                _today().strftime("%Y-%m-%d"),
                symbol,
                current_price,
                shares,
                daily_change_dollar,
                daily_change_pct,
            )

    # % of portfolio each holding represents (computed on a HOME_CURRENCY basis
    # so USD and SGD holdings compare fairly)
    for h in portfolio_data:
        h["pct_of_portfolio"] = round((h["_current_value_home"] / total_value * 100), 2) if total_value > 0 else 0

    # Best/worst performer of the day (compared by %, so currency doesn't skew it)
    best_performer = worst_performer = None
    if len(portfolio_data) >= 2:
        best_performer = max(portfolio_data, key=lambda h: h["daily_change_%"])
        worst_performer = min(portfolio_data, key=lambda h: h["daily_change_%"])

    # Sort by position size first (largest holdings first) for display — a
    # "what do I actually own" glance shouldn't be led by today's biggest
    # mover, which can bury your largest position under a small one that
    # happened to jump today.
    portfolio_data.sort(key=lambda h: h["_current_value_home"], reverse=True)

    unrealized_gain = total_value - total_cost_basis
    unrealized_gain_pct = (unrealized_gain / total_cost_basis * 100) if total_cost_basis > 0 else 0

    portfolio_daily_change = sum(h["_daily_change_home"] for h in portfolio_data)
    portfolio_daily_change_pct = (portfolio_daily_change / (total_value - portfolio_daily_change) * 100) if (total_value - portfolio_daily_change) > 0 else 0

    # Only record the day's aggregate if every holding priced successfully — a
    # partial total would silently corrupt the /week and /month baselines
    # (INSERT OR REPLACE keyed on date, with no memory a holding was missing).
    if save_snapshot and not failed_symbols:
        save_portfolio_aggregate(
            _today().strftime("%Y-%m-%d"),
            total_value,
            total_cost_basis,
            portfolio_daily_change,
            portfolio_daily_change_pct,
        )
    elif save_snapshot and failed_symbols:
        logger.warning(f"⚠️ Skipping portfolio aggregate for today — price fetch failed for: {failed_symbols}")

    # Drop internal-only conversion fields before returning
    for h in portfolio_data:
        h.pop("_current_value_home", None)
        h.pop("_daily_change_home", None)

    return {
        "total_value": round(total_value, 2),
        "total_cost_basis": round(total_cost_basis, 2),
        "unrealized_gain": round(unrealized_gain, 2),
        "unrealized_gain_pct": round(unrealized_gain_pct, 2),
        "daily_change_$": round(portfolio_daily_change, 2),
        "daily_change_%": round(portfolio_daily_change_pct, 2),
        "home_currency": HOME_CURRENCY,
        "fx_rates": fx_rates,
        "fx_warnings": fx_warnings,
        "failed_symbols": failed_symbols,
        "best_performer": best_performer,
        "worst_performer": worst_performer,
        "holdings": portfolio_data,
    }

def get_currency_breakdown(metrics):
    """Roll up portfolio value by currency, e.g. {'USD': 62.3, 'SGD': 37.7} (percent of total)."""
    totals = {}
    home_currency = metrics["home_currency"]
    for h in metrics["holdings"]:
        fx_rate = 1.0 if h["currency"] == home_currency else metrics["fx_rates"].get(h["currency"], 1.0)
        totals[h["currency"]] = totals.get(h["currency"], 0) + h["current_value"] * fx_rate

    total = sum(totals.values())
    if total <= 0:
        return {}
    return {ccy: round(v / total * 100, 1) for ccy, v in totals.items()}

def get_period_performance(days):
    """
    Compare current portfolio value against the earliest snapshot on/after
    `days` ago. Returns None if there isn't a snapshot old enough yet.

    The raw value change conflates market performance with deposits/
    withdrawals — adding a new position mid-period would otherwise show up
    entirely as "gain". net_contribution approximates that as the change in
    total cost basis since the start snapshot (new holdings add to it,
    removed ones subtract from it) and backs it out of the reported change,
    so this reflects investment performance, not account funding activity.
    """
    cutoff = (_today() - timedelta(days=days)).strftime("%Y-%m-%d")
    start = get_earliest_aggregate_since(cutoff)
    if not start:
        return None

    current = calculate_portfolio_metrics(save_snapshot=False)
    start_value = start["total_value"]
    net_contribution = current["total_cost_basis"] - start["total_cost_basis"]
    change = (current["total_value"] - start_value) - net_contribution
    change_pct = (change / start_value * 100) if start_value > 0 else 0

    return {
        "start_date": start["date"],
        "start_value": round(start_value, 2),
        "current_value": current["total_value"],
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "net_contribution": round(net_contribution, 2),
        "currency": HOME_CURRENCY,
    }
