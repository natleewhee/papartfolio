import asyncio
from datetime import date
import telegram_handler
from telegram_handler import chunk_message, send_telegram_message, _signals_population, _build_signals_section


# ---------- chunk_message ----------

def test_chunk_message_short_text_is_one_chunk():
    assert chunk_message("hello") == ["hello"]


def test_chunk_message_splits_on_line_boundaries():
    line = "x" * 100
    text = "\n".join([line] * 60)  # ~6000 chars, over the 4096 limit
    chunks = chunk_message(text, limit=4096)

    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)
    # No line was split mid-line — rejoining the chunks reconstructs the
    # original text exactly.
    assert "\n".join(chunks) == text


def test_chunk_message_hard_cuts_a_single_pathological_line():
    line = "y" * 10000
    chunks = chunk_message(line, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == line


# ---------- send_telegram_message ----------

class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_send_telegram_message_success(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return _FakeResponse(200)

    monkeypatch.setattr(telegram_handler.requests, "post", fake_post)

    ok = asyncio.run(send_telegram_message("hello"))

    assert ok is True
    assert len(calls) == 1
    assert calls[0]["parse_mode"] == "Markdown"


def test_send_telegram_message_falls_back_to_plain_text_on_parse_error(monkeypatch):
    """A Markdown-breaking symbol (e.g. from IBKR-sourced data that skips
    this bot's own symbol validation) shouldn't silently swallow the whole
    message — retry once as plain text instead."""
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        if json.get("parse_mode"):
            return _FakeResponse(400, "Bad Request: can't parse entities")
        return _FakeResponse(200)

    monkeypatch.setattr(telegram_handler.requests, "post", fake_post)

    ok = asyncio.run(send_telegram_message("some_weird*symbol"))

    assert ok is True
    assert len(calls) == 2
    assert calls[0].get("parse_mode") == "Markdown"
    assert "parse_mode" not in calls[1]


def test_send_telegram_message_fails_on_non_parse_error(monkeypatch):
    def fake_post(url, json, timeout):
        return _FakeResponse(403, "Forbidden: bot was blocked by the user")

    monkeypatch.setattr(telegram_handler.requests, "post", fake_post)

    ok = asyncio.run(send_telegram_message("hello"))

    assert ok is False


def test_send_telegram_message_sends_multiple_chunks(monkeypatch):
    line = "x" * 100
    text = "\n".join([line] * 60)  # over the 4096 limit
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return _FakeResponse(200)

    monkeypatch.setattr(telegram_handler.requests, "post", fake_post)

    ok = asyncio.run(send_telegram_message(text))

    assert ok is True
    assert len(calls) > 1
    assert all(len(c["text"]) <= telegram_handler.TELEGRAM_MESSAGE_LIMIT for c in calls)


# ---------- _signals_population / _build_signals_section ----------

def _no_watchlist(monkeypatch):
    monkeypatch.setattr(telegram_handler, "get_watchlist", lambda: [])


def _empty_checks(monkeypatch):
    """Neutral stand-ins for the support/EMA/earnings checks so a test can
    isolate just the piece it's exercising."""
    monkeypatch.setattr(telegram_handler, "resolve_support_levels_bulk", lambda items: {})
    monkeypatch.setattr(telegram_handler, "near_ema200_flags", lambda rows: [])
    monkeypatch.setattr(telegram_handler, "fetch_earnings_bulk", lambda symbols: {})
    monkeypatch.setattr(telegram_handler, "earnings_flags", lambda results: ([], []))


def test_signals_population_union_of_watchlist_and_holdings(monkeypatch):
    monkeypatch.setattr(
        telegram_handler, "get_watchlist",
        lambda: [{"symbol": "MSFT", "manual_st_support": None, "manual_mt_support": None}],
    )
    monkeypatch.setattr(telegram_handler, "get_prices_bulk", lambda symbols: {"MSFT": {"price": 300, "change_pct": 1.0}})
    metrics = {"holdings": [{"symbol": "AAPL", "current_price": 200, "daily_change_%": 2.0}]}

    population = _signals_population(metrics)

    symbols = {r["symbol"] for r in population}
    assert symbols == {"AAPL", "MSFT"}
    aapl = next(r for r in population if r["symbol"] == "AAPL")
    assert aapl["current_price"] == 200 and aapl["daily_change_%"] == 2.0
    msft = next(r for r in population if r["symbol"] == "MSFT")
    assert msft["current_price"] == 300 and msft["daily_change_%"] == 1.0


def test_signals_population_dedupes_held_and_watched_symbol(monkeypatch):
    monkeypatch.setattr(
        telegram_handler, "get_watchlist",
        lambda: [{"symbol": "AAPL", "manual_st_support": 150, "manual_mt_support": 140}],
    )
    monkeypatch.setattr(telegram_handler, "get_prices_bulk", lambda symbols: {})
    metrics = {"holdings": [{"symbol": "AAPL", "current_price": 200, "daily_change_%": 2.0}]}

    population = _signals_population(metrics)

    assert len(population) == 1
    assert population[0]["current_price"] == 200  # holding's live price wins, not a redundant quote fetch
    assert population[0]["manual_st"] == 150


def test_build_signals_section_movers_only(monkeypatch):
    """AE1: a mover with nothing else qualifying shows only the movers line."""
    _no_watchlist(monkeypatch)
    _empty_checks(monkeypatch)
    metrics = {"holdings": [
        {"symbol": "NVDA", "current_price": 100, "daily_change_%": 3.1},
        {"symbol": "AAPL", "current_price": 200, "daily_change_%": 0.5},
    ]}

    result = _build_signals_section(metrics)

    assert result == "📡 *Signals*\n🚀 Movers: NVDA +3.1%"


def test_build_signals_section_includes_holding_not_on_watchlist(monkeypatch):
    """AE2: support/resistance now covers holdings, not just the watchlist."""
    _no_watchlist(monkeypatch)
    monkeypatch.setattr(telegram_handler, "near_ema200_flags", lambda rows: [])
    monkeypatch.setattr(telegram_handler, "fetch_earnings_bulk", lambda symbols: {})
    monkeypatch.setattr(telegram_handler, "earnings_flags", lambda results: ([], []))
    monkeypatch.setattr(
        telegram_handler, "resolve_support_levels_bulk",
        lambda items: {"AAPL": {"short_term": {"level": 195, "distance_pct": 2.5}, "mid_term": None}},
    )
    metrics = {"holdings": [{"symbol": "AAPL", "current_price": 200, "daily_change_%": 0.1}]}

    result = _build_signals_section(metrics)

    assert "⚠️ Near support: AAPL (ST -2.5%)" in result


def test_build_signals_section_ema_only(monkeypatch):
    """AE3: a symbol near its 200 EMA but not near support/resistance shows
    only on the EMA line."""
    _no_watchlist(monkeypatch)
    monkeypatch.setattr(telegram_handler, "resolve_support_levels_bulk", lambda items: {})
    monkeypatch.setattr(telegram_handler, "near_ema200_flags", lambda rows: [("TSLA", 1.2)])
    monkeypatch.setattr(telegram_handler, "fetch_earnings_bulk", lambda symbols: {})
    monkeypatch.setattr(telegram_handler, "earnings_flags", lambda results: ([], []))
    metrics = {"holdings": [{"symbol": "TSLA", "current_price": 250, "daily_change_%": 0.2}]}

    result = _build_signals_section(metrics)

    assert result == "📡 *Signals*\n📊 Near 200 EMA: TSLA (1.2%)"


def test_build_signals_section_earnings_only(monkeypatch):
    """New: an upcoming earnings event with nothing else qualifying shows
    only the earnings line, prefixed with 📅 and folded into Signals (no
    separate Earnings Watch section exists anymore)."""
    _no_watchlist(monkeypatch)
    monkeypatch.setattr(telegram_handler, "resolve_support_levels_bulk", lambda items: {})
    monkeypatch.setattr(telegram_handler, "near_ema200_flags", lambda rows: [])
    monkeypatch.setattr(telegram_handler, "fetch_earnings_bulk", lambda symbols: {"NVDA": "fake-result"})
    next_event = {"date": date(2026, 1, 30), "days_until": 3, "timing": "after market close", "eps_estimate": 1.0}
    monkeypatch.setattr(telegram_handler, "earnings_flags", lambda results: ([("NVDA", next_event)], []))
    metrics = {"holdings": [{"symbol": "NVDA", "current_price": 100, "daily_change_%": 0.1}]}

    result = _build_signals_section(metrics)

    assert result.startswith("📡 *Signals*\n📅 ")
    assert "NVDA" in result


def test_build_signals_section_earnings_upcoming_before_recent(monkeypatch):
    """Upcoming events list before recent ones, matching earnings_flags'
    own return order."""
    _no_watchlist(monkeypatch)
    monkeypatch.setattr(telegram_handler, "resolve_support_levels_bulk", lambda items: {})
    monkeypatch.setattr(telegram_handler, "near_ema200_flags", lambda rows: [])
    monkeypatch.setattr(telegram_handler, "fetch_earnings_bulk", lambda symbols: {})
    next_event = {"date": date(2026, 1, 30), "days_until": 3, "timing": "after market close", "eps_estimate": 1.0}
    last_event = {"date": None, "days_since": 1, "eps_actual": 1.4, "eps_estimate": 1.35, "surprise_pct": 3.7}
    monkeypatch.setattr(
        telegram_handler, "earnings_flags",
        lambda results: ([("NVDA", next_event)], [("DRAM", last_event)]),
    )
    metrics = {"holdings": [
        {"symbol": "NVDA", "current_price": 100, "daily_change_%": 0.1},
        {"symbol": "DRAM", "current_price": 50, "daily_change_%": 0.1},
    ]}

    result = _build_signals_section(metrics)
    lines = result.split("\n")

    assert lines[1].startswith("📅") and "NVDA" in lines[1]
    assert lines[2].startswith("📅") and "DRAM" in lines[2]


def test_build_signals_section_empty_when_nothing_qualifies(monkeypatch):
    """AE4: nothing qualifying omits the section (and its header) entirely."""
    _no_watchlist(monkeypatch)
    _empty_checks(monkeypatch)
    metrics = {"holdings": [{"symbol": "AAPL", "current_price": 200, "daily_change_%": 0.1}]}

    assert _build_signals_section(metrics) == ""


def test_build_signals_section_no_holdings_or_watchlist(monkeypatch):
    _no_watchlist(monkeypatch)
    assert _build_signals_section({"holdings": []}) == ""


def test_build_signals_section_movers_support_ema_never_call_fmt_money(monkeypatch):
    """AE5: the movers/support/EMA lines carry only percentages/distances,
    never dollar formatting, so privacy mode needs no special handling for
    them. (Earnings lines are a separate exception: EPS figures are public
    company data, not portfolio values — same exemption /watch's support
    levels already had — and do use fmt_money via format_earnings_line;
    this test isolates the three price-action lines from that.)"""
    def _boom(*args, **kwargs):
        raise AssertionError("fmt_money should not be called from the movers/support/EMA lines")
    monkeypatch.setattr(telegram_handler, "fmt_money", _boom)
    _no_watchlist(monkeypatch)
    _empty_checks(monkeypatch)
    metrics = {"holdings": [{"symbol": "NVDA", "current_price": 100, "daily_change_%": 5.0}]}

    result = _build_signals_section(metrics)

    assert "NVDA" in result


def _boom_resolve(items):
    raise AssertionError("resolve_support_levels_bulk should not be called when support_results is given")


def test_build_signals_section_uses_given_support_results(monkeypatch):
    """A shared support_results (computed once in send_daily_report) must
    not trigger a second resolve_support_levels_bulk call here."""
    _no_watchlist(monkeypatch)
    monkeypatch.setattr(telegram_handler, "near_ema200_flags", lambda rows: [])
    monkeypatch.setattr(telegram_handler, "fetch_earnings_bulk", lambda symbols: {})
    monkeypatch.setattr(telegram_handler, "earnings_flags", lambda results: ([], []))
    monkeypatch.setattr(telegram_handler, "resolve_support_levels_bulk", _boom_resolve)
    metrics = {"holdings": [{"symbol": "AAPL", "current_price": 200, "daily_change_%": 0.1}]}
    population = _signals_population(metrics)
    support_results = {"AAPL": {"short_term": {"level": 195, "distance_pct": 2.5}, "mid_term": None}}

    result = _build_signals_section(metrics, population, support_results)

    assert "AAPL" in result
