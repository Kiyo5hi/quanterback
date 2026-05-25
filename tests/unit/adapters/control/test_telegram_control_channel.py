from __future__ import annotations

import pytest

from quanterback.adapters.control.telegram_control_channel import (
    TelegramControlChannel,
    parse_command,
)


def test_parse_freeze() -> None:
    cmd = parse_command({
        "message": {"text": "/freeze just testing",
                    "from": {"id": 42, "username": "alice"}},
    })
    assert cmd is not None
    assert cmd.command == "freeze"
    assert cmd.actor == "42"


def test_parse_unknown_command_returns_none() -> None:
    assert parse_command({"message": {"text": "/foo", "from": {"id": 1}}}) is None


def test_parse_non_message_returns_none() -> None:
    assert parse_command({"edited_message": {}}) is None


def test_parse_scan_with_args() -> None:
    cmd = parse_command({
        "message": {"text": "/scan aapl MSFT nvda", "from": {"id": 42}},
    })
    assert cmd is not None
    assert cmd.command == "scan"
    assert cmd.args == ("AAPL", "MSFT", "NVDA")  # uppercased


def test_parse_scan_no_args() -> None:
    cmd = parse_command({"message": {"text": "/scan", "from": {"id": 42}}})
    assert cmd is not None
    assert cmd.command == "scan"
    assert cmd.args == ()


def test_listen_yields_parsed_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        {"update_id": 1, "message": {"text": "/freeze",
                                       "from": {"id": 42}}},
        {"update_id": 2, "message": {"text": "/halt",
                                       "from": {"id": 42}}},
        {"update_id": 3, "message": {"text": "/random",
                                       "from": {"id": 42}}},
    ]
    served = {"index": 0}

    def fake_get(url: str, params: dict, timeout: float) -> object:
        i = served["index"]
        served["index"] += 1
        class R:
            status_code = 200
            def json(self):
                if i == 0:
                    return {"ok": True, "result": updates}
                # signal end of stream by raising
                raise StopIteration
        return R()

    monkeypatch.setattr(
        "quanterback.adapters.control.telegram_control_channel.requests.get",
        fake_get,
    )
    ch = TelegramControlChannel(token="t", max_iterations=1)
    cmds = list(ch.listen())
    kinds = [c.command for c in cmds]
    assert kinds == ["freeze", "halt"]


def test_parse_scan_with_single_ticker() -> None:
    cmd = parse_command({
        "message": {"text": "/scan TSLA", "from": {"id": 42}},
    })
    assert cmd is not None
    assert cmd.command == "scan"
    assert cmd.args == ("TSLA",)


def test_parse_scan_comma_separated_tickers() -> None:
    cmd = parse_command({
        "message": {"text": "/scan TSLA,NVDA,AMD", "from": {"id": 42}},
    })
    assert cmd is not None
    assert cmd.command == "scan"
    # Should be parsed as single arg if user writes without spaces
    assert cmd.args == ("TSLA,NVDA,AMD",)
