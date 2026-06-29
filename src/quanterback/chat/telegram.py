from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from quanterback.chat.models import ChatReply, ChatRequest
from quanterback.chat.service import ResearchChatService

log = logging.getLogger(__name__)


class TelegramResearchBot:
    def __init__(
        self,
        *,
        token: str,
        service: ResearchChatService,
        allowed_chat_ids: tuple[str, ...] = (),
        allowed_user_ids: tuple[str, ...] = (),
        poll_timeout: int = 25,
        max_workers: int = 8,
        max_iterations: int | None = None,
    ) -> None:
        self._token = token
        self._service = service
        self._allowed_chat_ids = set(allowed_chat_ids)
        self._allowed_user_ids = set(allowed_user_ids)
        self._poll_timeout = poll_timeout
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="chat")
        self._last_update_id = 0
        self._max_iterations = max_iterations
        self._get_updates = f"https://api.telegram.org/bot{token}/getUpdates"
        self._send_message = f"https://api.telegram.org/bot{token}/sendMessage"
        self._send_chat_action = f"https://api.telegram.org/bot{token}/sendChatAction"
        self._edit_message = f"https://api.telegram.org/bot{token}/editMessageText"
        self._answer_callback = f"https://api.telegram.org/bot{token}/answerCallbackQuery"

    def listen(self) -> None:
        for request in self._updates():
            if not self._is_authorized(request):
                self._reply(request, ChatReply(text=_authorization_error_text(request), ok=False))
                continue
            self._executor.submit(self._handle_one, request)

    def _is_authorized(self, request: ChatRequest) -> bool:
        if self._allowed_user_ids and request.external_user_id not in self._allowed_user_ids:
            log.info(
                "Ignoring research chat message from unauthorized user %s chat %s",
                request.external_user_id,
                request.external_chat_id,
            )
            return False
        if self._allowed_chat_ids and request.external_chat_id not in self._allowed_chat_ids:
            log.info(
                "Ignoring research chat message from unauthorized chat %s user %s",
                request.external_chat_id,
                request.external_user_id,
            )
            return False
        return True

    def _handle_one(self, request: ChatRequest) -> None:
        if request.callback_query_id:
            self._handle_callback(request)
            return
        done = threading.Event()
        status = _ProcessingStatus()
        action_thread = threading.Thread(
            target=self._keep_typing,
            args=(request, done),
            name="telegram-typing",
            daemon=True,
        )
        action_thread.start()
        status.message_id = self._send_status(
            request,
            "收到，我正在处理这条消息。",
        )
        try:
            reply = self._service.handle(request)
            done.set()
            if status.message_id is not None:
                self._edit(request, status.message_id, reply)
                self._bind_interaction_message(request, reply, status.message_id)
            else:
                sent_id = self._reply(request, reply)
                self._bind_interaction_message(request, reply, sent_id)
        except Exception as exc:
            done.set()
            log.exception("Research chat request failed: %s", exc)
            text = f"处理失败: {str(exc)[:300]}"
            if status.message_id is not None:
                self._edit(request, status.message_id, ChatReply(text=text, ok=False))
            else:
                self._reply(request, ChatReply(text=text, ok=False))

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
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            return self._callback_to_request(callback)
        msg = update.get("message")
        if not isinstance(msg, dict):
            return None
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        user = msg.get("from") or {}
        chat = msg.get("chat") or {}
        reply_to = msg.get("reply_to_message")
        reply_to_message_id = None
        if isinstance(reply_to, dict):
            raw_reply_id = reply_to.get("message_id")
            reply_to_message_id = int(raw_reply_id) if raw_reply_id is not None else None
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
            reply_to_message_id=reply_to_message_id,
            text=text,
            display_name=display_name,
            received_at=datetime.now(tz=timezone.utc),
        )

    def _callback_to_request(self, callback: dict) -> ChatRequest | None:
        data = callback.get("data")
        if not isinstance(data, str) or not data.strip():
            return None
        msg = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        user = callback.get("from") or {}
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
        message_id = int(msg.get("message_id", 0)) if isinstance(msg, dict) else 0
        return ChatRequest(
            provider="telegram",
            external_user_id=external_user_id,
            external_chat_id=external_chat_id,
            message_id=message_id,
            callback_query_id=str(callback.get("id") or ""),
            callback_data=data,
            text=data,
            display_name=display_name,
            received_at=datetime.now(tz=timezone.utc),
        )

    def _reply(self, request: ChatRequest, reply: ChatReply) -> int | None:
        sent_id: int | None = None
        chunks = _split_for_tg(reply.text)
        for idx, chunk in enumerate(chunks):
            payload = {
                "chat_id": request.external_chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "reply_to_message_id": request.message_id,
                "allow_sending_without_reply": True,
            }
            if idx == 0 and reply.inline_keyboard:
                payload["reply_markup"] = {"inline_keyboard": reply.inline_keyboard}
            try:
                resp = requests.post(self._send_message, json=payload, timeout=10)
                if not resp.ok:
                    log.warning("Research reply failed %d: %s; retrying plain",
                                resp.status_code, resp.text[:200])
                    payload.pop("parse_mode", None)
                    resp = requests.post(self._send_message, json=payload, timeout=10)
                if idx == 0 and resp.ok:
                    sent_id = _message_id_from_response(resp)
            except Exception as exc:
                log.warning("Research reply exception: %s", exc)
        return sent_id

    def _send_status(self, request: ChatRequest, text: str) -> int | None:
        payload = {
            "chat_id": request.external_chat_id,
            "text": text,
            "reply_to_message_id": request.message_id,
            "allow_sending_without_reply": True,
        }
        try:
            resp = requests.post(self._send_message, json=payload, timeout=10)
            if not resp.ok:
                log.warning("Research status message failed %d: %s",
                            resp.status_code, resp.text[:200])
                return None
            body = resp.json()
            result = body.get("result") if isinstance(body, dict) else None
            if isinstance(result, dict):
                message_id = result.get("message_id")
                return int(message_id) if message_id is not None else None
        except Exception as exc:
            log.warning("Research status message exception: %s", exc)
        return None

    def _edit(self, request: ChatRequest, message_id: int, reply: ChatReply) -> None:
        chunks = _split_for_tg(reply.text)
        first, rest = chunks[0], chunks[1:]
        payload = {
            "chat_id": request.external_chat_id,
            "message_id": message_id,
            "text": first,
            "parse_mode": "Markdown",
        }
        payload["reply_markup"] = {"inline_keyboard": reply.inline_keyboard or []}
        try:
            resp = requests.post(self._edit_message, json=payload, timeout=10)
            if not resp.ok:
                log.warning("Research edit failed %d: %s; retrying plain",
                            resp.status_code, resp.text[:200])
                payload.pop("parse_mode", None)
                requests.post(self._edit_message, json=payload, timeout=10)
        except Exception as exc:
            log.warning("Research edit exception: %s", exc)
            self._reply(request, ChatReply(text=first, inline_keyboard=reply.inline_keyboard))
        for chunk in rest:
            self._reply(request, ChatReply(text=chunk))

    def _bind_interaction_message(
        self, request: ChatRequest, reply: ChatReply, message_id: int | None,
    ) -> None:
        self._service.bind_interaction_message(
            pending_interaction_id=reply.pending_interaction_id,
            provider=request.provider,
            external_user_id=request.external_user_id,
            external_chat_id=request.external_chat_id,
            message_id=message_id,
        )

    def _answer_callback_query(self, callback_query_id: str) -> None:
        if not callback_query_id:
            return
        try:
            requests.post(
                self._answer_callback,
                json={"callback_query_id": callback_query_id},
                timeout=5,
            )
        except Exception as exc:
            log.debug("Research answerCallbackQuery failed: %s", exc)

    def _keep_typing(self, request: ChatRequest, done: threading.Event) -> None:
        while not done.is_set():
            payload = {
                "chat_id": request.external_chat_id,
                "action": "typing",
            }
            try:
                requests.post(self._send_chat_action, json=payload, timeout=5)
            except Exception as exc:
                log.debug("Research chat action failed: %s", exc)
            done.wait(4)

    def _handle_callback(self, request: ChatRequest) -> None:
        self._answer_callback_query(request.callback_query_id or "")
        done = threading.Event()
        action_thread = threading.Thread(
            target=self._keep_typing,
            args=(request, done),
            name="telegram-typing",
            daemon=True,
        )
        action_thread.start()
        self._edit(
            request,
            request.message_id,
            ChatReply(text="收到，我正在分析这个标的。"),
        )
        try:
            reply = self._service.handle(request)
            done.set()
            self._edit(request, request.message_id, reply)
            self._bind_interaction_message(request, reply, request.message_id)
        except Exception as exc:
            done.set()
            log.exception("Research chat callback failed: %s", exc)
            self._edit(
                request,
                request.message_id,
                ChatReply(text=f"处理失败: {str(exc)[:300]}", ok=False),
            )

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


def _message_id_from_response(resp: requests.Response) -> int | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return None
    message_id = result.get("message_id")
    return int(message_id) if message_id is not None else None


def _authorization_error_text(request: ChatRequest) -> str:
    return (
        "你还没有被授权使用这个 bot。\n\n"
        f"user_id: {request.external_user_id}\n"
        f"chat_id: {request.external_chat_id}\n\n"
        "请把这两个 ID 发给管理员开通白名单。"
    )


class _ProcessingStatus:
    message_id: int | None = None
