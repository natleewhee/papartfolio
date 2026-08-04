import ai_brief
from ai_brief import is_configured, generate_market_brief, _build_prompt, _typical_daily_move


# ---------- is_configured ----------

def test_is_configured_true_when_key_set(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    assert is_configured() is True


def test_is_configured_false_when_unset(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", None)
    assert is_configured() is False


# ---------- _typical_daily_move ----------

class _FakeCloses:
    """Mimics the slice of a yfinance DataFrame this module actually
    touches: df["Close"].tolist()."""
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values


class _FakeDF:
    def __init__(self, closes):
        self._closes = closes

    def __getitem__(self, key):
        assert key == "Close"
        return _FakeCloses(self._closes)

    def __len__(self):
        return len(self._closes)


def test_typical_daily_move_none_when_no_history(monkeypatch):
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: None)
    assert _typical_daily_move("AAPL") is None


def test_typical_daily_move_none_when_insufficient_history(monkeypatch):
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: _FakeDF([100.0] * 5))
    assert _typical_daily_move("AAPL") is None


def test_typical_daily_move_computes_stdev(monkeypatch):
    # Alternates +2%/-2% around 100, so daily returns have a known stdev of 2.0
    closes = [100.0]
    for i in range(21):
        closes.append(closes[-1] * (1.02 if i % 2 == 0 else 1 / 1.02))
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: _FakeDF(closes))

    typical = _typical_daily_move("AAPL")

    assert typical is not None
    assert 1.9 < typical < 2.1


# ---------- _build_prompt ----------

def test_build_prompt_includes_symbols_and_watchlist(monkeypatch):
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: None)
    holdings = [{"symbol": "AAPL", "daily_change_%": 1.2, "unrealized_gain_pct": 5.0}]
    prompt = _build_prompt(holdings, ["MSFT", "GOOG"])
    assert "AAPL" in prompt
    assert "MSFT, GOOG" in prompt


def test_build_prompt_no_watchlist_says_none():
    prompt = _build_prompt([], [])
    assert "none" in prompt


def test_build_prompt_includes_volatility_ratio_when_available(monkeypatch):
    closes = [100.0]
    for i in range(21):
        closes.append(closes[-1] * (1.02 if i % 2 == 0 else 1 / 1.02))
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: _FakeDF(closes))
    holdings = [{"symbol": "AAPL", "daily_change_%": 1.2, "unrealized_gain_pct": 5.0}]
    prompt = _build_prompt(holdings, [])
    assert "typical swing unavailable" not in prompt
    assert "normal today" in prompt


# ---------- generate_market_brief ----------

class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


class _FakeStream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_final_message(self):
        return self._response


class _FakeMessages:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    def stream(self, **kwargs):
        self._calls.append(kwargs)
        return _FakeStream(self._response)


class _FakeClient:
    def __init__(self, response, calls):
        self.messages = _FakeMessages(response, calls)


_HOLDINGS = [{"symbol": "AAPL", "daily_change_%": 1.2, "unrealized_gain_pct": 5.0}]


def test_generate_market_brief_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", None)
    assert generate_market_brief(_HOLDINGS, []) is None


def test_generate_market_brief_none_when_nothing_to_brief(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    assert generate_market_brief([], []) is None


def test_generate_market_brief_returns_text_on_success(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: None)
    response = _FakeResponse("end_turn", [_FakeTextBlock("Some brief text.")])
    calls = []
    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response, calls))

    result = generate_market_brief(_HOLDINGS, ["MSFT"])

    assert result == "Some brief text."
    assert len(calls) == 1
    assert calls[0]["model"] == ai_brief.MODEL
    assert calls[0]["max_tokens"] == ai_brief.MAX_TOKENS
    assert calls[0]["tools"][0]["type"] == "web_search_20260209"
    assert calls[0]["thinking"] == {"type": "adaptive"}


def test_generate_market_brief_none_on_refusal(monkeypatch):
    """Safety-classifier refusals should degrade to 'nothing to show', the
    same as any other optional report section — not raise or crash the
    report."""
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: None)
    response = _FakeResponse("refusal", [])
    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response, []))

    assert generate_market_brief(_HOLDINGS, []) is None


def test_generate_market_brief_none_on_empty_text(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: None)
    response = _FakeResponse("end_turn", [_FakeTextBlock("   ")])
    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response, []))

    assert generate_market_brief(_HOLDINGS, []) is None


def test_generate_market_brief_none_on_exception(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_brief, "_fetch_history", lambda symbol: None)

    def _boom(**kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", _boom)

    assert generate_market_brief(_HOLDINGS, []) is None
