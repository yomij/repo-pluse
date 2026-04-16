import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
import lark_oapi as lark
import lark_oapi.api.auth.v3 as auth_v3
import lark_oapi.api.im.v1 as im_v1


@dataclass(frozen=True)
class FeishuChat:
    chat_id: str
    name: str
    description: str = ""
    external: bool = False


class FeishuClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        chat_id: str,
        base_url: str = 'https://open.feishu.cn/open-apis',
        http_client: Optional[httpx.AsyncClient] = None,
        token_refresh_buffer_seconds: int = 60,
        oapi_client=None,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self.chat_id = chat_id
        self._base_url = base_url.rstrip('/')
        self._token_refresh_buffer_seconds = token_refresh_buffer_seconds
        self._http_client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._owns_http_client = http_client is None
        self._oapi_client = oapi_client or _build_oapi_client(
            app_id=app_id,
            app_secret=app_secret,
            base_url=base_url,
        )
        self._tenant_access_token: Optional[str] = None
        self._tenant_access_token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def tenant_access_token(self, force_refresh: bool = False) -> str:
        now = time.time()
        if (
            not force_refresh
            and self._tenant_access_token
            and now < self._tenant_access_token_expires_at
        ):
            return self._tenant_access_token

        async with self._token_lock:
            now = time.time()
            if (
                not force_refresh
                and self._tenant_access_token
                and now < self._tenant_access_token_expires_at
            ):
                return self._tenant_access_token

            response = await self._http_client.post(
                f'{self._base_url}/auth/v3/tenant_access_token/internal/',
                json={'app_id': self._app_id, 'app_secret': self._app_secret},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get('code') != 0:
                raise RuntimeError('Failed to get Feishu tenant access token')

            token = payload.get('tenant_access_token')
            if not token:
                raise RuntimeError('Feishu tenant access token missing in response')

            expire = int(payload.get('expire', 0) or 0)
            ttl = max(expire - self._token_refresh_buffer_seconds, 0)

            self._tenant_access_token = token
            self._tenant_access_token_expires_at = now + ttl
            return token

    async def _authorized_headers(self) -> Dict[str, str]:
        token = await self.tenant_access_token()
        return {'Authorization': f'Bearer {token}'}

    async def send_text(self, text: str, receive_id: Optional[str] = None) -> Dict[str, Any]:
        request = im_v1.CreateMessageRequest.builder().receive_id_type('chat_id').request_body(
            im_v1.CreateMessageRequestBody.builder()
            .receive_id(receive_id or self.chat_id)
            .msg_type('text')
            .content(json.dumps({'text': text}, ensure_ascii=False))
            .build()
        ).build()
        response = await self._oapi_client.im.v1.message.acreate(request)
        self._raise_on_feishu_response_error(response, 'send text')
        return _plainify(response)

    async def send_post(
        self,
        title: str,
        markdown: str,
        receive_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        request = im_v1.CreateMessageRequest.builder().receive_id_type('chat_id').request_body(
            im_v1.CreateMessageRequestBody.builder()
            .receive_id(receive_id or self.chat_id)
            .msg_type('post')
            .content(
                json.dumps(
                    {
                        'zh_cn': {
                            'title': title,
                            'content': [[{'tag': 'md', 'text': markdown}]],
                        }
                    },
                    ensure_ascii=False,
                )
            )
            .build()
        ).build()
        response = await self._oapi_client.im.v1.message.acreate(request)
        self._raise_on_feishu_response_error(response, 'send post')
        return _plainify(response)

    async def add_reaction(self, message_id: str, emoji_type: str) -> Dict[str, Any]:
        request = im_v1.CreateMessageReactionRequest.builder().message_id(message_id).request_body(
            im_v1.CreateMessageReactionRequestBody(
                {'reaction_type': {'emoji_type': emoji_type}}
            )
        ).build()
        response = await self._oapi_client.im.v1.message_reaction.acreate(request)
        self._raise_on_feishu_response_error(response, 'add reaction')
        return _plainify(response)

    async def remove_reaction(self, message_id: str, reaction_id: str) -> Dict[str, Any]:
        request = im_v1.DeleteMessageReactionRequest.builder().message_id(message_id).reaction_id(
            reaction_id
        ).build()
        response = await self._oapi_client.im.v1.message_reaction.adelete(request)
        self._raise_on_feishu_response_error(response, 'remove reaction')
        return _plainify(response)

    async def send_card(self, card: Dict[str, Any], receive_id: Optional[str] = None) -> Dict[str, Any]:
        request = im_v1.CreateMessageRequest.builder().receive_id_type('chat_id').request_body(
            im_v1.CreateMessageRequestBody.builder()
            .receive_id(receive_id or self.chat_id)
            .msg_type('interactive')
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        ).build()
        response = await self._oapi_client.im.v1.message.acreate(request)
        self._raise_on_feishu_response_error(response, 'send card')
        return _plainify(response)

    async def reply_text(self, receive_id: str, text: str) -> Dict[str, Any]:
        return await self.send_text(text, receive_id=receive_id)

    async def list_chats(self, page_size: int = 100) -> list[FeishuChat]:
        headers = await self._authorized_headers()
        chats: list[FeishuChat] = []
        page_token: Optional[str] = None

        while True:
            params = {"page_size": min(max(page_size, 1), 100)}
            if page_token:
                params["page_token"] = page_token

            response = await self._http_client.get(
                f"{self._base_url}/im/v1/chats",
                headers=headers,
                params=params,
            )
            payload = self._decode_feishu_payload(response, "list chats")
            self._raise_on_feishu_business_error(payload, "list chats")

            data = payload.get("data") or {}
            for item in data.get("items") or []:
                chat_id = str(item.get("chat_id") or "").strip()
                if not chat_id:
                    continue
                chats.append(
                    FeishuChat(
                        chat_id=chat_id,
                        name=str(item.get("name") or "").strip() or chat_id,
                        description=str(item.get("description") or "").strip(),
                        external=bool(item.get("external", False)),
                    )
                )

            if not data.get("has_more"):
                return chats

            page_token = str(data.get("page_token") or "").strip()
            if not page_token:
                return chats

    def _decode_feishu_payload(self, response: httpx.Response, operation: str) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            response.raise_for_status()
            raise RuntimeError(
                "Feishu API returned invalid JSON while trying to {0}".format(operation)
            ) from exc

        if response.is_error:
            self._raise_on_feishu_business_error(payload, operation)
            response.raise_for_status()

        return payload

    def _raise_on_feishu_business_error(self, payload: Dict[str, Any], operation: str) -> None:
        if payload.get('code') == 0:
            return
        code = payload.get('code')
        msg = payload.get('msg') or payload.get('message') or 'unknown error'
        raise RuntimeError('Feishu API failed to {0}: code={1}, msg={2}'.format(operation, code, msg))

    @staticmethod
    def _raise_on_feishu_response_error(response: Any, operation: str) -> None:
        if response.success():
            return
        code = getattr(response, 'code', None)
        msg = getattr(response, 'msg', None) or 'unknown error'
        raise RuntimeError(
            'Feishu API failed to {0}: code={1}, msg={2}'.format(operation, code, msg)
        )


def _build_oapi_client(app_id: str, app_secret: str, base_url: str):
    domain = base_url.rstrip('/')
    if domain.endswith('/open-apis'):
        domain = domain[: -len('/open-apis')]

    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .domain(domain)
        .build()
    )


def _plainify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {key: _plainify(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_plainify(item) for item in value]
    if hasattr(value, '__dict__'):
        return {
            key: _plainify(item)
            for key, item in vars(value).items()
            if not key.startswith('_') and item is not None
        }
    return value
