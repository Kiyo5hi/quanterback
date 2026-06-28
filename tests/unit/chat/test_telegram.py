from __future__ import annotations

from datetime import datetime, timezone

from quanterback.chat.models import ChatRequest
from quanterback.chat.telegram import TelegramResearchBot, _authorization_error_text


def _request(*, user_id: str = "u1", chat_id: str = "c1") -> ChatRequest:
    return ChatRequest(
        provider="telegram",
        external_user_id=user_id,
        external_chat_id=chat_id,
        message_id=1,
        text="分析 NVDA",
        display_name="Alice",
        received_at=datetime.now(tz=timezone.utc),
    )


def test_telegram_research_bot_authorizes_allowed_user_and_chat() -> None:
    bot = TelegramResearchBot(
        token="token",
        service=object(),  # type: ignore[arg-type]
        allowed_user_ids=("u1",),
        allowed_chat_ids=("c1",),
    )

    assert bot._is_authorized(_request(user_id="u1", chat_id="c1")) is True
    assert bot._is_authorized(_request(user_id="u2", chat_id="c1")) is False
    assert bot._is_authorized(_request(user_id="u1", chat_id="c2")) is False


def test_telegram_research_bot_allows_all_users_when_allowlist_empty() -> None:
    bot = TelegramResearchBot(
        token="token",
        service=object(),  # type: ignore[arg-type]
        allowed_chat_ids=("c1",),
    )

    assert bot._is_authorized(_request(user_id="u2", chat_id="c1")) is True


def test_authorization_error_text_includes_user_and_chat_ids() -> None:
    text = _authorization_error_text(_request(user_id="8024680950", chat_id="-1001"))

    assert "没有被授权" in text
    assert "user_id: 8024680950" in text
    assert "chat_id: -1001" in text


def test_telegram_request_parses_reply_to_message_id() -> None:
    bot = TelegramResearchBot(token="token", service=object())  # type: ignore[arg-type]

    request = bot._to_request({
        "message": {
            "message_id": 10,
            "text": "1",
            "reply_to_message": {"message_id": 99},
            "from": {"id": 123, "username": "alice"},
            "chat": {"id": -1001},
        }
    })

    assert request is not None
    assert request.text == "1"
    assert request.message_id == 10
    assert request.reply_to_message_id == 99


def test_telegram_request_parses_callback_query() -> None:
    bot = TelegramResearchBot(token="token", service=object())  # type: ignore[arg-type]

    request = bot._to_request({
        "callback_query": {
            "id": "cb1",
            "data": "ticker_choice:abc123:2",
            "from": {"id": 123, "username": "alice"},
            "message": {
                "message_id": 99,
                "chat": {"id": -1001},
            },
        }
    })

    assert request is not None
    assert request.text == "ticker_choice:abc123:2"
    assert request.callback_query_id == "cb1"
    assert request.callback_data == "ticker_choice:abc123:2"
    assert request.message_id == 99
