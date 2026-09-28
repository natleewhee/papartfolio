import asyncio
from bot_handlers import _suggest_command, _reply_chunked, _parse_add_args, BOT_COMMANDS


def test_suggest_command_finds_prefix_match():
    assert _suggest_command("recon") == "reconcile"


def test_suggest_command_finds_close_typo():
    assert _suggest_command("wachlist") == "watchlist"


def test_suggest_command_none_when_nothing_close():
    assert _suggest_command("xyzzyplugh") is None


def test_suggest_command_uses_provided_list():
    assert _suggest_command("addd", known_commands=["add", "remove"]) == "add"


def test_bot_commands_satisfy_telegram_constraints():
    """BotCommand requires: command 1-32 chars, lowercase letters/digits/
    underscores only; description 3-256 chars. A violation here would make
    set_my_commands() fail at startup for every command, not just one."""
    for name, description in BOT_COMMANDS:
        assert 1 <= len(name) <= 32, name
        assert name == name.lower(), name
        assert all(c.isalnum() or c == "_" for c in name), name
        assert 3 <= len(description) <= 256, name


def test_bot_commands_names_are_unique():
    names = [name for name, _ in BOT_COMMANDS]
    assert len(names) == len(set(names))


# ---------- _parse_add_args ----------

def test_parse_add_args_accepts_fractional_shares():
    """Fractional shares are real (DRIP, fractional-share brokers, and IBKR
    reconciliation can hand one back) — /add used to force int() and reject
    or silently truncate them."""
    assert _parse_add_args("AAPL 12.734 148.10") == ("AAPL", 12.734, 148.10)


def test_parse_add_args_whole_number_still_works():
    assert _parse_add_args("AAPL 50 12.50") == ("AAPL", 50.0, 12.50)


# ---------- _reply_chunked ----------

class _FakeMessage:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, parse_mode=None):
        self.sent.append((text, parse_mode))


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMessage()


def test_reply_chunked_sends_one_message_when_short():
    update = _FakeUpdate()
    asyncio.run(_reply_chunked(update, "hello", parse_mode="Markdown"))
    assert update.message.sent == [("hello", "Markdown")]


def test_reply_chunked_splits_a_long_message():
    """A big enough /list, /watchlist, or /earnings sweep would otherwise
    build a single message Telegram rejects outright for exceeding the
    ~4096-char limit, silently dropping the whole reply."""
    update = _FakeUpdate()
    line = "x" * 100
    text = "\n".join([line] * 60)  # well over 4096 chars
    asyncio.run(_reply_chunked(update, text, parse_mode="Markdown"))
    assert len(update.message.sent) > 1
    assert all(len(t) <= 4096 for t, _ in update.message.sent)
    assert all(mode == "Markdown" for _, mode in update.message.sent)


# ---------- alert suggestions / buttons ----------

from types import SimpleNamespace

import bot_handlers
from bot_handlers import alert_suggestion_keyboard, on_button


def test_alert_suggestion_keyboard_levels():
    support = {"short_term": {"level": 182.456}, "mid_term": None}
    resistance = {"short_term": {"level": 205.0}}
    kb = alert_suggestion_keyboard("AAPL", support, resistance)
    buttons = [row[0] for row in kb.inline_keyboard]
    assert [b.callback_data for b in buttons] == ["alert:AAPL:below:182.46", "alert:AAPL:above:205.0"]
    assert "ST support" in buttons[0].text and "$182.46" in buttons[0].text
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)  # Telegram limit


def test_alert_suggestion_keyboard_none_when_no_levels():
    assert alert_suggestion_keyboard("AAPL", None, {"short_term": None}) is None


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((text, kwargs))


class _FakeQuery:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.answered = []

    async def answer(self, *args, **kwargs):
        self.answered.append((args, kwargs))


def _press(data, user_id=None):
    query = _FakeQuery(data, bot_handlers.TELEGRAM_USER_ID if user_id is None else user_id)
    context = SimpleNamespace(bot=_FakeBot())
    asyncio.run(on_button(SimpleNamespace(callback_query=query), context))
    return query, context.bot.sent


def test_on_button_alert_sets_alert(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_handlers, "_set_price_alert", lambda s, d, t: calls.append((s, d, t)) or "✅ set")
    _, sent = _press("alert:AAPL:below:182.46")
    assert calls == [("AAPL", "below", 182.46)]
    assert sent[0][0] == "✅ set"


def test_on_button_levels_replies_with_keyboard(monkeypatch):
    async def fake_levels(symbol):
        return f"*{symbol} levels*", "KB"
    monkeypatch.setattr(bot_handlers, "_levels_reply", fake_levels)
    _, sent = _press("levels:NVDA")
    text, kwargs = sent[0]
    assert text == "*NVDA levels*" and kwargs["reply_markup"] == "KB" and kwargs["parse_mode"] == "Markdown"


def test_on_button_rejects_other_users(monkeypatch):
    monkeypatch.setattr(bot_handlers, "_set_price_alert", lambda *a: (_ for _ in ()).throw(AssertionError("should not be called")))
    query, sent = _press("alert:AAPL:below:1", user_id=bot_handlers.TELEGRAM_USER_ID + 1)
    assert sent == []
    assert query.answered[0][1].get("show_alert") is True


def test_alert_suggestion_keyboard_keeps_sub_dollar_precision():
    kb = alert_suggestion_keyboard("SHIB-USD", {"short_term": {"level": 0.0000123456}, "mid_term": None}, None)
    button = kb.inline_keyboard[0][0]
    assert button.callback_data == "alert:SHIB-USD:below:1.235e-05"
    assert float(button.callback_data.split(":")[-1]) > 0
