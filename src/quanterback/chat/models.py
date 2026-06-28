from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = "telegram"
    external_user_id: str
    external_chat_id: str
    message_id: int = 0
    reply_to_message_id: int | None = None
    callback_query_id: str | None = None
    callback_data: str | None = None
    text: str
    display_name: str | None = None
    received_at: datetime


class ChatIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["tool", "confirm", "cancel", "help", "unknown"]
    tool_name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class ChatReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    ok: bool = True
    confirmation_required: bool = False
    inline_keyboard: list[list[dict[str, str]]] | None = None
    pending_interaction_id: str | None = None
