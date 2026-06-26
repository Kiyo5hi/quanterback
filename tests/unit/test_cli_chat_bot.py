from __future__ import annotations


def test_cli_registers_chat_bot_command() -> None:
    from quanterback.cli import main

    try:
        main(["chat-bot", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

