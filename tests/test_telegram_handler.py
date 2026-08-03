import asyncio
import telegram_handler
from telegram_handler import chunk_message, send_telegram_message


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
