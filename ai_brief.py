"""AI-generated market brief: overnight macro and company-specific news for
holdings and watchlist, synthesized via the Claude API with web search.

The numbers (portfolio table, prices, gains) stay entirely the bot's own
deterministic data from Finnhub/yfinance — this module only produces the one
thing those feeds can't: connecting a move to *why* it happened, using live
web search. Silently disabled if ANTHROPIC_API_KEY isn't set, same pattern as
the IBKR integration.
"""
import logging
from datetime import date
import anthropic
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_TOKENS = 8000


def is_configured():
    return bool(ANTHROPIC_API_KEY)


def _build_prompt(holdings, watchlist_symbols):
    holding_lines = [
        f"- {h['symbol']}: {h['daily_change_%']:+.1f}% today, "
        f"{h['unrealized_gain_pct']:+.1f}% overall gain"
        for h in holdings
    ]
    watch_line = ", ".join(watchlist_symbols) if watchlist_symbols else "none"
    return (
        f"Today is {date.today().isoformat()}. Research current market news with "
        "web search and write a concise morning briefing for a retail investor "
        "holding:\n" + "\n".join(holding_lines) +
        f"\n\nAlso watching (not held): {watch_line}\n\n"
        "Cover, in this order, in plain text with short section headers in ALL "
        "CAPS (this renders in a Telegram message, not a browser — no markdown "
        "headers, no tables):\n"
        "1. OVERNIGHT & MACRO — genuinely market-moving overnight news (rates, "
        "geopolitics, major indices), 2-4 sentences. Only include this section "
        "if something notable actually happened.\n"
        "2. WHAT HITS YOUR HOLDINGS — for each holding or watchlist symbol with "
        "real news since the last session (earnings, guidance, analyst notes, "
        "sector-moving competitor news), one short paragraph connecting the "
        "news to that specific position. Skip any symbol with nothing new — "
        "don't pad this section out.\n"
        "3. TODAY'S CALENDAR — anything scheduled today that could move these "
        "positions (earnings, Fed events, major economic data).\n"
        "4. THE ONE THING — the single most decision-relevant item from above, "
        "only if one clearly stands out.\n\n"
        "Be concrete and skip any section with nothing genuinely new to say — "
        "this is a daily briefing, not a status report padded out to look "
        "complete."
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
