import asyncio
import requests
from telegram.ext import ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, TIMEZONE
from portfolio import calculate_portfolio_metrics, fmt_money, format_holdings_table, get_currency_breakdown
from portfolio_db import get_setting, get_watchlist
from fetcher import get_currency_for_symbol, get_prices_bulk, fetch_extended_hours_bulk
from support import (
    resolve_support_levels_bulk, format_support_table, near_support_flags, format_near_support_line,
    near_ema200_flags, format_near_ema_line,
)
from earnings import fetch_earnings_bulk, earnings_flags, format_earnings_line
from ai_brief import generate_market_brief, is_configured as ai_brief_configured
from datetime import datetime
import pytz
import logging

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
MOVER_THRESHOLD_PCT = 3.0  # flat threshold, deliberately independent of ai_brief's relative-volatility definition

def chunk_message(text, limit=TELEGRAM_MESSAGE_LIMIT):
    """Split `text` into Telegram-safe chunks, breaking on line boundaries so
    a line doesn't get sliced in half. A large enough portfolio/watchlist
    would otherwise build a single message Telegram rejects outright for
    being over the ~4096-char limit, silently dropping it. A single
    pathologically long line (longer than `limit` on its own) is hard-cut as
    a last resort."""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = []
    current_len = 0
    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 for the newline that'll rejoin it
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        if line_len - 1 > limit:
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            continue
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks

async def send_telegram_message(text: str, parse_mode: str = "Markdown"):
    """Send a message via the Telegram Bot API.

    Runs the (blocking) HTTP call off the event loop — this bot's own
    scheduler and Telegram long-polling share that loop, so a slow send
    would otherwise stall both. Falls back to a plain-text retry if Telegram
    rejects the message for a Markdown parse error (a stray `_`/`*` in a
    symbol or error string, most likely from IBKR-sourced data that never
    goes through this bot's own symbol validation) — this bot's only
    interface is Telegram messages, so a formatting glitch shouldn't
    silently swallow the whole thing. Long messages are chunked so a big
    enough portfolio/watchlist doesn't get rejected outright for exceeding
    Telegram's per-message length limit.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    async def _send(chunk, mode):
        payload = {"chat_id": TELEGRAM_USER_ID, "text": chunk}
        if mode:
            payload["parse_mode"] = mode
        return await asyncio.to_thread(requests.post, url, json=payload, timeout=10)

    ok = True
    for chunk in chunk_message(text):
        try:
            response = await _send(chunk, parse_mode)
            if response.status_code == 200:
                continue

            if parse_mode and "can't parse entities" in response.text.lower():
                logger.warning("⚠️ Markdown parse error sending Telegram message — retrying this chunk as plain text")
                response = await _send(chunk, None)
                if response.status_code == 200:
                    continue

            logger.error(f"❌ Telegram API error: {response.text}")
            ok = False
        except Exception as e:
            logger.error(f"❌ Error sending message: {e}")
            ok = False

    if ok:
        logger.info("✅ Message sent to Telegram")
    return ok

def _signals_population(metrics, watchlist_rows=None):
    """One row per symbol in {watchlist} ∪ {holdings}, each with the price
    data both _build_signals_section and _build_support_section need.
    Holdings resolve directly from metrics; watchlist-only symbols get a
    live quote (concurrently). Computed once per report and passed to both
    callers (along with `watchlist_rows`) so a watchlist-only symbol needs
    exactly one quote fetch and one watchlist read, not one per section."""
    if watchlist_rows is None:
        watchlist_rows = get_watchlist()
    manual_by_symbol = {w["symbol"]: (w.get("manual_st_support"), w.get("manual_mt_support")) for w in watchlist_rows}
    watched = [w["symbol"] for w in watchlist_rows]

    held = {h["symbol"]: h for h in metrics["holdings"]}
    population = list(dict.fromkeys(watched + list(held.keys())))  # union, dedup, stable order

    resolved_price = {s: held[s]["current_price"] for s in population if s in held}
    resolved_change_pct = {s: held[s]["daily_change_%"] for s in population if s in held}
    missing = [s for s in population if s not in resolved_price]
    if missing:
        quotes = get_prices_bulk(missing)
        for s, q in quotes.items():
            resolved_price[s] = q["price"] if q and q.get("price") else None
            resolved_change_pct[s] = q["change_pct"] if q and q.get("change_pct") is not None else None

    return [
        {
            "symbol": s,
            "current_price": resolved_price.get(s),
            "daily_change_%": resolved_change_pct.get(s),
            "manual_st": manual_by_symbol.get(s, (None, None))[0],
            "manual_mt": manual_by_symbol.get(s, (None, None))[1],
        }
        for s in population
    ]

def _resolve_signals_support(population):
    """resolve_support_levels_bulk() over the full population, keyed by
    symbol — shared by _build_signals_section and _build_support_section so
    a watchlist symbol's support/resistance (swing points + MAs, not just
    the underlying history fetch) is computed once per report, not twice."""
    return resolve_support_levels_bulk(
        (r["symbol"], r["current_price"], r["manual_st"], r["manual_mt"]) for r in population
    )

def _build_support_section(metrics, population=None, support_results=None, watchlist_rows=None):
    """Compact support table for the daily report, covering only stocks
    explicitly on the watchlist — holding a stock no longer implies tracking
    its support levels; that's now a deliberate /watch action. Support levels
    are public market prices (not portfolio values), so they're shown even in
    privacy mode. Returns "" if the watchlist is empty.

    `population` (from _signals_population), `support_results` (from
    _resolve_signals_support), and `watchlist_rows` (from get_watchlist())
    are computed locally when not given, so this function still works
    standalone (tests, other callers)."""
    if watchlist_rows is None:
        watchlist_rows = get_watchlist()
    if not watchlist_rows:
        return ""
    watched = [w["symbol"] for w in watchlist_rows]

    if population is None:
        population = _signals_population(metrics, watchlist_rows)
    resolved_price = {r["symbol"]: r["current_price"] for r in population}
    resolved_change_pct = {r["symbol"]: r["daily_change_%"] for r in population}

    if support_results is None:
        support_results = _resolve_signals_support(population)

    rows = []
    for symbol in watched:
        data = support_results.get(symbol)
        current_price = resolved_price.get(symbol)
        if current_price is None and data:
            current_price = data["current_price"]  # history-derived fallback (yesterday's close)
        rows.append({
            "symbol": symbol,
            "currency": get_currency_for_symbol(symbol),
            "current_price": current_price,
            "daily_change_%": resolved_change_pct.get(symbol),
            "short_term": data["short_term"] if data else None,
            "mid_term": data["mid_term"] if data else None,
        })

    table = format_support_table(rows, fmt_money)
    return (
        "\n📉 *Watchlist — Support Levels*\n"
        f"```\n{table}\n```\n"
        "_ST=short · MT=mid support (below price)_"
    )

def _build_signals_section(metrics, population=None, support_results=None):
    """Consolidated 'what needs attention today' section — big movers,
    support/resistance proximity, and 200 EMA proximity across holdings and
    watchlist — leading the report so the signal isn't buried under routine
    numbers. Replaces the old best/worst line and the near-support flag
    line that used to trail the watchlist support table. Movers use a flat
    threshold, deliberately independent of the AI brief's own
    relative-to-volatility definition. Returns "" if nothing qualifies.

    `population` (from _signals_population) and `support_results` (from
    _resolve_signals_support) are computed locally when not given, so this
    function still works standalone (tests, other callers)."""
    if population is None:
        population = _signals_population(metrics)
    if not population:
        return ""

    movers = [
        r for r in population
        if r["daily_change_%"] is not None and abs(r["daily_change_%"]) >= MOVER_THRESHOLD_PCT
    ]
    movers.sort(key=lambda r: -abs(r["daily_change_%"]))
    movers_line = (
        "🚀 Movers: " + ", ".join(f"{r['symbol']} {r['daily_change_%']:+.1f}%" for r in movers)
        if movers else ""
    )

    if support_results is None:
        support_results = _resolve_signals_support(population)
    support_rows = [
        {
            "symbol": r["symbol"],
            "short_term": (support_results.get(r["symbol"]) or {}).get("short_term"),
            "mid_term": (support_results.get(r["symbol"]) or {}).get("mid_term"),
        }
        for r in population
    ]
    support_line = format_near_support_line(near_support_flags(support_rows))

    ema_line = format_near_ema_line(near_ema200_flags(population))

    lines = [line for line in (movers_line, support_line, ema_line) if line]
    if not lines:
        return ""
    return "📡 *Signals*\n" + "\n".join(lines)

def _build_ai_brief_section(metrics):
    """AI-generated overnight/company news synthesis via Claude + web search —
    the one thing Finnhub/yfinance can't provide (why a stock moved, not just
    that it did). Silently omitted if ANTHROPIC_API_KEY isn't configured, or
    if nothing came back (refusal, API error, or genuinely nothing to say)."""
    if not ai_brief_configured():
        return ""
    watched = [w["symbol"] for w in get_watchlist()]
    brief = generate_market_brief(metrics["holdings"], watched)
    if not brief:
        return ""
    return "🌐 *AI Market Brief*\n" + brief

def _build_extended_hours_section(metrics, privacy=False):
    """Pre/post-market movement for held US tickers (SGX has no extended-hours
    sessions). Most relevant right at the report's 20:30 SGT send time, which
    sits inside the US pre-market window. Silently omits a holding when
    extended-hours data isn't available (outside those windows, or the
    slower yfinance lookup failed/timed out). Returns "" if nothing to show."""
    us_symbols = [h["symbol"] for h in metrics["holdings"] if h["currency"] == "USD"]
    if not us_symbols:
        return ""

    results = fetch_extended_hours_bulk(us_symbols)
    rows = [(sym, r) for sym, r in sorted(results.items()) if r]
    if not rows:
        return ""

    lines = ["", "🌅 *Pre/Post-Market (USD)*"]
    for sym, r in rows:
        label = "Pre" if r["market_state"] == "PRE" else "Post"
        price_str = fmt_money(r["price"], "USD", privacy)
        pct = r["change_pct"]
        if not privacy and pct is not None:
            emoji = "🟢" if pct >= 0 else "🔴"
            lines.append(f"{sym}: {label} {price_str} {emoji} {pct:+.2f}%")
        else:
            lines.append(f"{sym}: {label} {price_str}")
    return "\n".join(lines)

def _build_earnings_section(metrics):
    """Earnings-release radar for held + watchlist stocks: flags anything
    reporting within the next 2 weeks, or that reported within the last few
    days. Most stocks have nothing to say on a given day, so — unlike the
    support table — this is a flagged list, silent for everything else.
    Returns "" if nothing qualifies."""
    held = [h["symbol"] for h in metrics["holdings"]]
    watched = [w["symbol"] for w in get_watchlist()]
    symbols = sorted(set(held) | set(watched))
    if not symbols:
        return ""

    results = fetch_earnings_bulk(symbols)
    upcoming, recent = earnings_flags(results)
    if not upcoming and not recent:
        return ""

    lines = ["", "📅 *Earnings Watch*"]
    for symbol, next_event in upcoming:
        currency = get_currency_for_symbol(symbol)
        lines.append(format_earnings_line(symbol, currency, fmt_money, next_event=next_event))
    for symbol, last_event in recent:
        currency = get_currency_for_symbol(symbol)
        lines.append(format_earnings_line(symbol, currency, fmt_money, last_event=last_event))
    return "\n".join(lines)

async def send_daily_report(context: ContextTypes.DEFAULT_TYPE = None):
    """Generate and send daily portfolio report."""
    try:
        privacy = get_setting("privacy_mode", "0") == "1"
        compact = get_setting("report_style", "full") == "compact"

        # Calculate metrics (blocking network calls — off the event loop,
        # which this scheduler shares with Telegram's own long polling)
        metrics = await asyncio.to_thread(calculate_portfolio_metrics)

        if not metrics["holdings"]:
            msg = "📭 Portfolio is empty. Use /add to add holdings."
            await send_telegram_message(msg)
            return

        home_currency = metrics["home_currency"]
        emoji = "🟢" if metrics["daily_change_%"] >= 0 else "🔴"

        # Computed once and shared between _build_signals_section and
        # _build_support_section below, so a watchlist symbol gets exactly
        # one watchlist read, one live quote fetch, and one support/
        # resistance computation per report, not one per section.
        watchlist_rows = await asyncio.to_thread(get_watchlist)
        population = await asyncio.to_thread(_signals_population, metrics, watchlist_rows)
        support_results = await asyncio.to_thread(_resolve_signals_support, population)

        # AI brief and Signals run first so they lead the report — a reader's
        # eye goes to what changed before the raw numbers, not after them.
        ai_brief_section = await asyncio.to_thread(_build_ai_brief_section, metrics)
        signals_section = await asyncio.to_thread(_build_signals_section, metrics, population, support_results)

        # Summary
        today = datetime.now(pytz.timezone(TIMEZONE))
        report = f"📊 *{today.strftime('%d %b %Y')}* ({home_currency})\n\n"
        if ai_brief_section:
            report += ai_brief_section + "\n\n"
        if signals_section:
            report += signals_section + "\n\n"
        report += (
            f"Total: {fmt_money(metrics['total_value'], home_currency, privacy)}\n"
            f"Today: {emoji} {fmt_money(metrics['daily_change_$'], home_currency, privacy, show_sign=True)} "
            f"({metrics['daily_change_%']:+.2f}%)\n"
            f"Gain: {fmt_money(metrics['unrealized_gain'], home_currency, privacy, show_sign=True)} "
            f"({metrics['unrealized_gain_pct']:+.2f}%)\n"
        )

        for currency, rate in metrics["fx_rates"].items():
            report += f"FX: 1 {currency} = {fmt_money(rate, home_currency, decimals=4)}\n"

        breakdown = get_currency_breakdown(metrics)
        if len(breakdown) > 1:
            mix = " / ".join(f"{ccy} {pct:.0f}%" for ccy, pct in sorted(breakdown.items(), key=lambda x: -x[1]))
            report += f"Currency Mix: {mix}\n"

        if metrics["fx_warnings"]:
            warned = ", ".join(metrics["fx_warnings"])
            report += f"⚠️ No live FX rate for {warned} — assumed 1:1, total may be off\n"

        if metrics["failed_symbols"]:
            report += f"⚠️ Price unavailable: {', '.join(metrics['failed_symbols'])}\n"

        if not compact:
            report += "\n" + f"```\n{format_holdings_table(metrics['holdings'], privacy)}\n```"

        extended_hours_section = await asyncio.to_thread(_build_extended_hours_section, metrics, privacy)
        if extended_hours_section:
            report += "\n" + extended_hours_section

        earnings_section = await asyncio.to_thread(_build_earnings_section, metrics)
        if earnings_section:
            report += "\n" + earnings_section

        support_section = await asyncio.to_thread(_build_support_section, metrics, population, support_results, watchlist_rows)
        if support_section:
            report += "\n" + support_section

        await send_telegram_message(report)
        logger.info("✅ Daily report sent")

    except Exception as e:
        logger.error(f"❌ Error generating report: {e}")
        await send_telegram_message(f"❌ Error generating report: {e}")
