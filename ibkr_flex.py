"""IBKR Flex Web Service integration: end-of-day holdings reconciliation.

Pulls real open positions from Interactive Brokers via a pre-configured Flex
Query and reconciles them against the bot's holdings table, so /add entries
never drift from what's actually in the account. The Flex Web Service is
read-only reporting — it has no order/trading capability at all, even if the
token leaked.

Requires IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID (see config.py for setup
steps); the feature is silently disabled if either is unset.

IBKR only refreshes Flex "Activity" data once daily, at their own EOD
close-of-business processing — running this more than once a day just
re-reads the same snapshot, which is harmless (not wasted beyond one extra
API call), not something to design around.
"""
import time
import logging
import xml.etree.ElementTree as ET
import requests
from config import IBKR_FLEX_TOKEN, IBKR_FLEX_QUERY_ID
from portfolio_db import get_all_holdings, add_holding, update_holding, remove_holding
from telegram_handler import send_telegram_message

logger = logging.getLogger(__name__)

SEND_REQUEST_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
GET_STATEMENT_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"

GET_STATEMENT_RETRIES = 5
GET_STATEMENT_RETRY_DELAY_SECONDS = 5

SUPPORTED_CURRENCIES = ("USD", "SGD")  # everything else, the bot can't price


def is_configured():
    return bool(IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID)


def _request_statement():
    """Kick off Flex report generation. Returns (reference_code, error) —
    reference_code is None on failure, with `error` describing why (surfaced
    all the way up to the Telegram summary, so a bad token/query ID is
    diagnosable without digging through server logs)."""
    try:
        response = requests.get(
            SEND_REQUEST_URL,
            params={"t": IBKR_FLEX_TOKEN, "q": IBKR_FLEX_QUERY_ID, "v": "3"},
            timeout=15,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        status = root.findtext("Status")
        if status != "Success":
            error_msg = root.findtext("ErrorMessage") or "unknown error"
            error_code = root.findtext("ErrorCode")
            full_error = f"{error_msg} (code {error_code})" if error_code else error_msg
            logger.error(f"❌ IBKR Flex SendRequest failed: {full_error}")
            return None, full_error
        return root.findtext("ReferenceCode"), None
    except Exception as e:
        logger.error(f"❌ IBKR Flex SendRequest error: {e}")
        return None, str(e)


def _fetch_statement(reference_code):
    """Poll for the generated report — IBKR generates it asynchronously,
    typically within seconds but occasionally longer. Returns (xml_text,
    error) — xml_text is None if it never became ready, with `error`
    describing the last issue seen."""
    last_error = None
    for attempt in range(GET_STATEMENT_RETRIES):
        try:
            response = requests.get(
                GET_STATEMENT_URL,
                params={"q": reference_code, "t": IBKR_FLEX_TOKEN, "v": "3"},
                timeout=15,
            )
            response.raise_for_status()
            text = response.text
            # A not-ready-yet or error response is the short
            # <FlexStatementResponse> wrapper; the real report is
            # <FlexQueryResponse> — cheap to tell apart by root tag alone.
            root = ET.fromstring(text)
            if root.tag == "FlexQueryResponse":
                return text, None
            last_error = root.findtext("ErrorMessage") or "not ready"
            logger.info(f"ℹ️ IBKR Flex statement not ready yet ({last_error}), retrying...")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"⚠️ IBKR Flex GetStatement attempt {attempt + 1} failed: {e}")
        if attempt < GET_STATEMENT_RETRIES - 1:
            time.sleep(GET_STATEMENT_RETRY_DELAY_SECONDS)
    logger.error(f"❌ IBKR Flex statement never became ready: {last_error}")
    return None, last_error


def _parse_positions(xml_text):
    """Extract stock positions from a Flex OpenPositions report. Returns a
    list of {"symbol", "shares", "avg_cost", "currency"}, skipping non-stock
    rows, rows missing fields we need, and currencies the bot can't price.
    Returns None if the XML can't be parsed at all."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"❌ IBKR Flex report XML parse error: {e}")
        return None

    positions = []
    for el in root.iter("OpenPosition"):
        if el.get("assetCategory") != "STK":
            continue

        symbol = el.get("symbol")
        currency = el.get("currency")
        position = el.get("position")
        cost_price = el.get("costBasisPrice") or el.get("avgCost") or el.get("averageCost")
        if not symbol or not currency or position is None or cost_price is None:
            logger.warning(f"⚠️ Skipping IBKR position with missing fields: {symbol!r}")
            continue

        if currency not in SUPPORTED_CURRENCIES:
            logger.warning(f"⚠️ Skipping IBKR position {symbol} — unsupported currency {currency}")
            continue

        try:
            shares = int(float(position))
            avg_cost = float(cost_price)
        except ValueError:
            continue
        if shares <= 0:
            continue  # closed position — the diff in reconcile_holdings() handles this as a removal

        # SGX tickers use the bot's yfinance-style ".SI" suffix; IBKR's own
        # symbol for the same instrument doesn't include it.
        if currency == "SGD" and not symbol.upper().endswith(".SI"):
            symbol = f"{symbol.upper()}.SI"

        positions.append({
            "symbol": symbol.upper(),
            "shares": shares,
            "avg_cost": round(avg_cost, 4),
            "currency": currency,
        })
    return positions


def fetch_flex_positions():
    """End-to-end Flex fetch: request -> poll -> parse. Returns
    (positions, error) — positions is None if the fetch failed at any stage,
    with `error` describing why."""
    reference_code, error = _request_statement()
    if not reference_code:
        return None, error
    xml_text, error = _fetch_statement(reference_code)
    if not xml_text:
        return None, error
    positions = _parse_positions(xml_text)
    if positions is None:
        return None, "malformed XML in Flex report"
    return positions, None


def reconcile_holdings():
    """Fetch real IBKR positions and reconcile the bot's holdings table to
    match: add anything missing, update anything with different shares/cost,
    remove anything no longer held.

    Refuses to apply if IBKR's report parses to zero stock positions while
    the bot currently holds something — that pattern is far more likely a
    fetch/parse failure than a real full liquidation, and auto-applying it
    would silently wipe the whole portfolio.

    Returns:
    {
        "status": "ok" | "skipped_empty" | "fetch_failed" | "not_configured",
        "added": [{"symbol", "shares", "avg_cost", "currency"}, ...],
        "updated": [{"symbol", "from": {...}, "to": {...}}, ...],
        "removed": [{"symbol", "shares", "avg_cost", "currency"}, ...],
        "error": str | None,  # only meaningful when status == "fetch_failed"
    }
    """
    if not is_configured():
        return {"status": "not_configured", "added": [], "updated": [], "removed": []}

    ibkr_positions, error = fetch_flex_positions()
    if ibkr_positions is None:
        return {"status": "fetch_failed", "added": [], "updated": [], "removed": [], "error": error}

    current = {h["symbol"]: h for h in get_all_holdings()}
    ibkr_by_symbol = {p["symbol"]: p for p in ibkr_positions}

    if not ibkr_by_symbol and current:
        logger.warning(
            "⚠️ IBKR Flex report parsed to zero stock positions while the bot "
            f"holds {len(current)} — treating as a likely fetch/parse issue, "
            "not touching holdings."
        )
        return {"status": "skipped_empty", "added": [], "updated": [], "removed": []}

    added, updated, removed = [], [], []

    for symbol, pos in ibkr_by_symbol.items():
        existing = current.get(symbol)
        if existing is None:
            if add_holding(symbol, pos["shares"], pos["avg_cost"], currency=pos["currency"]):
                added.append(pos)
        elif existing["shares"] != pos["shares"] or abs(existing["avg_cost"] - pos["avg_cost"]) > 0.0001:
            if update_holding(symbol, pos["shares"], pos["avg_cost"]):
                updated.append({"symbol": symbol, "from": existing, "to": pos})

    for symbol, existing in current.items():
        if symbol not in ibkr_by_symbol:
            if remove_holding(symbol):
                removed.append(existing)

    return {"status": "ok", "added": added, "updated": updated, "removed": removed}


def _format_summary(result):
    """Telegram-ready summary of a reconcile_holdings() result. Returns None
    when there's nothing worth sending (feature disabled)."""
    status = result["status"]
    if status == "not_configured":
        return None
    if status == "fetch_failed":
        detail = f"\nReason: {result['error']}" if result.get("error") else ""
        return (
            "⚠️ *IBKR Reconciliation*\n\n"
            "Couldn't fetch the Flex report — holdings unchanged. "
            "Will retry at the next scheduled sync."
            f"{detail}"
        )
    if status == "skipped_empty":
        return (
            "⚠️ *IBKR Reconciliation*\n\n"
            "IBKR returned zero stock positions while you currently hold some — "
            "this looks like a fetch/parse issue, not a real liquidation. "
            "Holdings left unchanged; please check manually."
        )

    added, updated, removed = result["added"], result["updated"], result["removed"]
    if not added and not updated and not removed:
        return "✅ *IBKR Reconciliation*\n\nHoldings already match IBKR — no changes."

    lines = ["🔄 *IBKR Reconciliation*", ""]
    for p in added:
        lines.append(f"➕ Added {p['symbol']}: {p['shares']} @ {p['avg_cost']:.2f} {p['currency']}")
    for u in updated:
        frm, to = u["from"], u["to"]
        lines.append(
            f"✏️ Updated {u['symbol']}: {frm['shares']}→{to['shares']} shares, "
            f"avg cost {frm['avg_cost']:.2f}→{to['avg_cost']:.2f}"
        )
    for h in removed:
        lines.append(f"➖ Removed {h['symbol']} (no longer held)")
    return "\n".join(lines)


async def run_reconciliation():
    """Scheduled entry point: reconcile against IBKR and notify the outcome.
    Silent no-op if the feature isn't configured. Also used directly by
    /reconcile for on-demand testing — same code path, same summary."""
    try:
        result = reconcile_holdings()
        summary = _format_summary(result)
        if summary:
            await send_telegram_message(summary)
        logger.info(
            f"✅ IBKR reconciliation run: {result['status']} "
            f"(+{len(result['added'])} ~{len(result['updated'])} -{len(result['removed'])})"
        )
    except Exception as e:
        logger.error(f"❌ Error during IBKR reconciliation: {e}")
        await send_telegram_message(f"❌ Error during IBKR reconciliation: {e}")
