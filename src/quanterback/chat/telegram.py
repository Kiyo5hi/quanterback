from __future__ import annotations

import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from quanterback.chat.models import ChatRequest
from quanterback.chat.service import ResearchChatService

log = logging.getLogger(__name__)


class TelegramResearchBot:
    def __init__(
        self,
        *,
        token: str,
        service: ResearchChatService,
        allowed_chat_ids: tuple[str, ...] = (),
        poll_timeout: int = 25,
        max_workers: int = 8,
        max_iterations: int | None = None,
    ) -> None:
        self._token = token
        self._service = service
        self._allowed_chat_ids = set(allowed_chat_ids)
        self._poll_timeout = poll_timeout
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="chat")
        self._last_update_id = 0
        self._max_iterations = max_iterations
        self._get_updates = f"https://api.telegram.org/bot{token}/getUpdates"
        self._send_message = f"https://api.telegram.org/bot{token}/sendMessage"

    def listen(self) -> None:
        for request in self._updates():
            if self._allowed_chat_ids and request.external_chat_id not in self._allowed_chat_ids:
                log.info("Ignoring research chat message from unauthorized chat %s",
                         request.external_chat_id)
                continue
            self._executor.submit(self._handle_one, request)

    def _handle_one(self, request: ChatRequest) -> None:
        try:
            reply = self._service.handle(request)
            self._reply(request, reply.text)
        except Exception as exc:
            log.exception("Research chat request failed: %s", exc)
            self._reply(request, f"处理失败: {str(exc)[:300]}")

    def _updates(self) -> Iterable[ChatRequest]:
        iters = 0
        while True:
            if self._max_iterations is not None and iters >= self._max_iterations:
                return
            iters += 1
            try:
                resp = requests.get(
                    self._get_updates,
                    params={
                        "offset": self._last_update_id + 1,
                        "timeout": self._poll_timeout,
                    },
                    timeout=self._poll_timeout + 10,
                )
                payload = resp.json()
            except Exception as exc:
                log.warning("Research TG getUpdates failed: %s", exc)
                continue
            if not payload.get("ok"):
                continue
            for update in payload.get("result", []):
                uid = int(update.get("update_id", 0))
                self._last_update_id = max(self._last_update_id, uid)
                request = self._to_request(update)
                if request is not None:
                    yield request

    def _to_request(self, update: dict) -> ChatRequest | None:
        msg = update.get("message")
        if not isinstance(msg, dict):
            return None
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        user = msg.get("from") or {}
        chat = msg.get("chat") or {}
        external_user_id = str(user.get("id") or chat.get("id") or "unknown")
        external_chat_id = str(chat.get("id") or external_user_id)
        display_name = (
            user.get("username")
            or " ".join(
                p for p in (user.get("first_name"), user.get("last_name")) if p
            )
            or None
        )
        return ChatRequest(
            provider="telegram",
            external_user_id=external_user_id,
            external_chat_id=external_chat_id,
            message_id=int(msg.get("message_id", 0)),
            text=text,
            display_name=display_name,
            received_at=datetime.now(tz=timezone.utc),
        )

    def _reply(self, request: ChatRequest, text: str) -> None:
        for chunk in _split_for_tg(text):
            payload = {
                "chat_id": request.external_chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "reply_to_message_id": request.message_id,
                "allow_sending_without_reply": True,
            }
            try:
                resp = requests.post(self._send_message, json=payload, timeout=10)
                if not resp.ok:
                    log.warning("Research reply failed %d: %s; retrying plain",
                                resp.status_code, resp.text[:200])
                    payload.pop("parse_mode", None)
                    requests.post(self._send_message, json=payload, timeout=10)
            except Exception as exc:
                log.warning("Research reply exception: %s", exc)


def _split_for_tg(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    cur = text
    while len(cur) > limit:
        cut = cur.rfind("\n", 0, limit)
        if cut < 1000:
            cut = limit
        out.append(cur[:cut])
        cur = cur[cut:].lstrip()
    if cur:
        out.append(cur)
    return out
