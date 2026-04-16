import asyncio
import json
import logging
import threading
from typing import Any, Callable, Optional

from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

logger = logging.getLogger(__name__)


class FeishuLongConnectionClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        container,
        encrypt_key: str = "",
        verification_token: str = "",
        domain: str = "https://open.feishu.cn",
        event_handler_builder_factory: Callable[..., Any] = EventDispatcherHandler.builder,
        ws_client_factory: Optional[Callable[..., Any]] = None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.container = container
        self.encrypt_key = encrypt_key
        self.verification_token = verification_token
        self.domain = domain
        self.event_handler_builder_factory = event_handler_builder_factory
        self.ws_client_factory = ws_client_factory
        self.thread_factory = thread_factory
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._started = False

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        if self._started:
            return

        self._loop = loop
        event_handler = (
            self.event_handler_builder_factory(
                self.encrypt_key,
                self.verification_token,
            )
            .register_p2_im_message_receive_v1(self._handle_message_event_sync)
            .build()
        )
        self._started = True

        if self.ws_client_factory is None:
            self._thread = self.thread_factory(
                target=lambda: self._run_real_client(event_handler),
                name="feishu-long-connection",
                daemon=True,
            )
            self._thread.start()
            logger.info("Feishu long connection client started in background thread")
            return

        self._client = self.ws_client_factory(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=event_handler,
            domain=self.domain,
        )
        self._client.start()

    def stop(self) -> None:
        self._started = False

    async def handle_message_event(self, data) -> None:
        payload = self._adapt_message_event(data)
        if payload is None:
            return
        message = payload["event"]["message"]
        logger.info(
            "Feishu long connection received text message: message_id=%s",
            message.get("message_id"),
        )
        await self.container.handle_event(payload)

    def _handle_message_event_sync(self, data) -> None:
        coroutine = self.handle_message_event(data)
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
            future.add_done_callback(self._log_future_exception)
            return

        asyncio.run(coroutine)

    def _run_real_client(self, event_handler) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            import lark_oapi.ws.client as lark_ws_client_module
            import lark_oapi.ws as lark_ws

            lark_ws_client_module.loop = loop
            self._client = lark_ws.Client(
                app_id=self.app_id,
                app_secret=self.app_secret,
                event_handler=event_handler,
                domain=self.domain,
            )
            self._client.start()
        except Exception:
            logger.exception("Feishu long connection client stopped unexpectedly")

    @staticmethod
    def _adapt_message_event(data) -> Optional[dict[str, Any]]:
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        if message is None:
            return None

        if getattr(message, "message_type", None) != "text":
            return None

        text = _extract_text(getattr(message, "content", None))
        if not text:
            return None

        payload_message = {
            "message_id": getattr(message, "message_id", None),
            "text": text,
            "chat_type": getattr(message, "chat_type", None),
        }
        mentions = _plainify_mentions(getattr(message, "mentions", None))
        if mentions:
            payload_message["mentions"] = mentions

        return {
            "event": {
                "chat_id": getattr(message, "chat_id", None),
                "message": payload_message,
            }
        }

    @staticmethod
    def _log_future_exception(future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Failed to handle Feishu long connection message event")


def _extract_text(content: Optional[str]) -> str:
    if not content:
        return ""

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content

    text = payload.get("text") if isinstance(payload, dict) else None
    return text if isinstance(text, str) else ""


def _plainify_mentions(mentions) -> list[dict[str, Any]]:
    plain_mentions: list[dict[str, Any]] = []
    for mention in mentions or []:
        mention_id = getattr(mention, "id", None)
        plain_mentions.append(
            {
                "key": getattr(mention, "key", None),
                "id": {
                    "open_id": getattr(mention_id, "open_id", None),
                    "union_id": getattr(mention_id, "union_id", None),
                    "user_id": getattr(mention_id, "user_id", None),
                },
                "name": getattr(mention, "name", None),
                "tenant_key": getattr(mention, "tenant_key", None),
            }
        )
    return plain_mentions
