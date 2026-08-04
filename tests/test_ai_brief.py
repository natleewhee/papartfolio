import ai_brief
from ai_brief import is_configured, generate_market_brief, _build_prompt


# ---------- is_configured ----------

def test_is_configured_true_when_key_set(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    assert is_configured() is True


def test_is_configured_false_when_unset(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", None)
    assert is_configured() is False


# ---------- _build_prompt ----------

def test_build_prompt_includes_symbols_and_watchlist():
    holdings = [{"symbol": "AAPL", "daily_change_%": 1.2, "unrealized_gain_pct": 5.0}]
    prompt = _build_prompt(holdings, ["MSFT", "GOOG"])
    assert "AAPL" in prompt
    assert "MSFT, GOOG" in prompt


def test_build_prompt_no_watchlist_says_none():
    prompt = _build_prompt([], [])
    assert "none" in prompt


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
    response = _FakeResponse("end_turn", [_FakeTextBlock("Some brief text.")])
    calls = []
    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response, calls))

    result = generate_market_brief(_HOLDINGS, ["MSFT"])

    assert result == "Some brief text."
    assert len(calls) == 1
    assert calls[0]["model"] == ai_brief.MODEL
    assert calls[0]["tools"][0]["type"] == "web_search_20260209"
    assert calls[0]["thinking"] == {"type": "adaptive"}


def test_generate_market_brief_none_on_refusal(monkeypatch):
    """Safety-classifier refusals should degrade to 'nothing to show', the
    same as any other optional report section — not raise or crash the
    report."""
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    response = _FakeResponse("refusal", [])
    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response, []))

    assert generate_market_brief(_HOLDINGS, []) is None


def test_generate_market_brief_none_on_empty_text(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")
    response = _FakeResponse("end_turn", [_FakeTextBlock("   ")])
    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response, []))

    assert generate_market_brief(_HOLDINGS, []) is None


def test_generate_market_brief_none_on_exception(monkeypatch):
    monkeypatch.setattr(ai_brief, "ANTHROPIC_API_KEY", "sk-test")

    def _boom(**kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(ai_brief.anthropic, "Anthropic", _boom)

    assert generate_market_brief(_HOLDINGS, []) is None
