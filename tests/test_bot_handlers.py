from bot_handlers import _suggest_command, BOT_COMMANDS


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
