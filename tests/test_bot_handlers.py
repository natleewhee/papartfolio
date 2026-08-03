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
