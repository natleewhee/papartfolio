"""AI-generated market brief: macro news, holdings that moved outside their
normal range or had thesis-relevant news, and big market news elsewhere —
synthesized via the Claude API with web search.

The numbers (portfolio table, prices, gains) stay entirely the bot's own
deterministic data from Finnhub/yfinance — this module only produces the one
thing those feeds can't: connecting a move to *why* it happened, using live
web search, and filtering holdings to only those whose move is large
relative to their own normal daily volatility (not a fixed % across every
symbol). Silently disabled if ANTHROPIC_API_KEY isn't set, same pattern as
the IBKR integration.
"""
import logging
import statistics
from datetime import date
import anthropic
from config import ANTHROPIC_API_KEY
from support import _fetch_history

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_TOKENS = 2000  # tight 3-section brief needs far less than a full report
VOLATILITY_WINDOW = 20  # trading days of returns behind today's move


def is_configured():
    return bool(ANTHROPIC_API_KEY)


def key_preview():
    """Masked first/last-4 view of the key this running process actually
    sees (e.g. 'sk-a...f8sd'), for /settings — lets the user cross-check
    it against what they typed on Render without exposing the full key."""
    if not ANTHROPIC_API_KEY:
        return None
    key = ANTHROPIC_API_KEY
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


def _typical_daily_move(symbol):
    """20-trading-day stdev of daily % returns — today's move measured
    against this stock's own normal range, not a fixed % cutoff applied the
    same way to every symbol. None if history is unavailable."""
    df = _fetch_history(symbol)
    if df is None or len(df) < VOLATILITY_WINDOW + 1:
        return None
    closes = df["Close"].tolist()[-(VOLATILITY_WINDOW + 1):]
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        for i in range(1, len(closes))
    ]
    return statistics.pstdev(returns) if len(returns) >= 2 else None


def _holding_lines(holdings):
    lines = []
    for h in holdings:
        symbol = h["symbol"]
        chg = h["daily_change_%"]
        typical = _typical_daily_move(symbol)
        if typical and typical > 0:
            ratio = abs(chg) / typical
            move_note = f"typical daily swing ~{typical:.1f}%, {ratio:.1f}x normal today"
        else:
            move_note = "typical swing unavailable"
        lines.append(
            f"- {symbol}: {chg:+.1f}% today ({move_note}), "
            f"{h['unrealized_gain_pct']:+.1f}% overall gain"
        )
    return lines


def _build_prompt(holdings, watchlist_symbols):
    holding_lines = _holding_lines(holdings)
    watch_line = ", ".join(watchlist_symbols) if watchlist_symbols else "none"
    return (
        f"Today is {date.today().isoformat()}. Research current market news with "
        "web search and write a short, tightly-filtered morning briefing for a "
        "retail investor. This is a plain-text Telegram message — no markdown "
        "headers, no tables, no bullet symbols, just short section labels in "
        "ALL CAPS.\n\n"
        "Their holdings, with today's move and how large that move is "
        "relative to this stock's own normal daily swing (20-trading-day "
        "volatility):\n" + "\n".join(holding_lines) +
        f"\n\nAlso watching (not held, mention only if something major "
        f"happened): {watch_line}\n\n"
        "Write exactly these sections, in this order, and keep the whole "
        "brief under ~150 words total:\n\n"
        "MACRO\n"
        "1-2 sentences on genuinely market-moving overnight/macro news "
        "(rates, central banks, geopolitics, major indices), plus the one "
        "scheduled event today that could matter, if any. Omit this section "
        "entirely if nothing notable happened.\n\n"
        "YOUR HOLDINGS\n"
        "One short line per holding, but ONLY include a holding if either: "
        "(a) today's move is meaningfully outside its normal daily swing "
        "(roughly 1.5x+ its typical daily move, using the ratio given "
        "above), or (b) there's genuine thesis-relevant news since the last "
        "session — earnings, guidance change, a major analyst call, a "
        "regulatory or competitive event — even if the price move itself is "
        "small. Do not list a holding just because you have something "
        "generic to say; skip anything routine or already priced in. End "
        "each line you do include with a short verdict: 'thesis "
        "strengthened', 'thesis unchanged', or 'thesis weakened'. If "
        "nothing qualifies across the whole portfolio, write one line: "
        "'Nothing moved outside its normal range today.'\n\n"
        "ELSEWHERE\n"
        "Up to 2 short items on genuinely big market news NOT tied to their "
        "holdings or watchlist — anywhere in the market, any size company, "
        "any sector. Think 'what would a market-literate friend flag you' — "
        "a huge earnings beat/miss, major M&A, a big single-stock move with "
        "a real story behind it. Omit this section if nothing rises to that "
        "bar.\n\n"
        "Be concrete and brief. Any section may be entirely omitted if "
        "there's truly nothing that clears its bar — this is a short daily "
        "briefing, not a status report padded out to look complete."
    )


def generate_market_brief(holdings, watchlist_symbols):
    """Research overnight/company news relevant to `holdings` and
    `watchlist_symbols` and return a narrative brief, or None if unavailable
    (not configured, nothing to brief, refused by safety classifiers, or an
    API error) — the caller treats this like any other optional report
    section: omit it, don't fail the report."""
    if not is_configured():
        return None
    if not holdings and not watchlist_symbols:
        return None

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = _build_prompt(holdings, watchlist_symbols)
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "refusal":
            logger.warning("⚠️ AI brief request refused by safety classifiers")
            return None

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None
    except Exception as e:
        logger.error(f"❌ Error generating AI brief: {e}")
        return None
