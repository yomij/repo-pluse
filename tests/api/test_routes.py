import json
import asyncio

import httpx
import lark_oapi.api.im.v1 as im_v1
import pytest
from fastapi.testclient import TestClient

from repo_pulse.feishu.client import FeishuClient
from repo_pulse.main import create_app


class FakeContainer:
    def __init__(self):
        self.event_payloads = []
        self.action_payloads = []
        self.digest_called = 0

    def handle_event(self, payload):
        self.event_payloads.append(payload)

    def handle_action(self, payload):
        self.action_payloads.append(payload)

    def run_digest_now(self):
        self.digest_called += 1


class AsyncContainer:
    def __init__(self):
        self.action_payloads = []

    async def handle_action(self, payload):
        await asyncio.sleep(0)
        self.action_payloads.append(payload)


class _AsyncMethod:
    def __init__(self, response_factory):
        self.calls = []
        self._response_factory = response_factory

    async def __call__(self, request, option=None):
        self.calls.append((request, option))
        return self._response_factory(request, option)


class _Namespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_healthz_ok():
    app = create_app(container=FakeContainer())
    client = TestClient(app)

    response = client.get('/healthz')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_feishu_events_challenge_echoed():
    app = create_app(container=FakeContainer())
    client = TestClient(app)

    payload = {'challenge': 'abc123'}
    response = client.post('/webhooks/feishu/events', json=payload)

    assert response.status_code == 200
    assert response.json() == {'challenge': 'abc123'}


def test_feishu_events_with_container_triggers_handle_event():
    container = FakeContainer()
    app = create_app(container=container)
    client = TestClient(app)

    payload = {'event': {'message': {'text': 'hello'}}}
    response = client.post('/webhooks/feishu/events', json=payload)

    assert response.status_code == 200
    assert response.json() == {'ok': True}
    assert container.event_payloads == [payload]


def test_feishu_events_with_message_content_triggers_handle_event():
    container = FakeContainer()
    app = create_app(container=container)
    client = TestClient(app)

    payload = {'event': {'message': {'content': json.dumps({'text': '/help'})}}}
    response = client.post('/webhooks/feishu/events', json=payload)

    assert response.status_code == 200
    assert response.json() == {'ok': True}
    assert container.event_payloads == [payload]


def test_feishu_actions_with_container_triggers_handle_action():
    container = FakeContainer()
    app = create_app(container=container)
    client = TestClient(app)

    payload = {'action': {'value': 'clicked'}}
    response = client.post('/webhooks/feishu/actions', json=payload)

    assert response.status_code == 200
    assert response.json() == {}
    assert container.action_payloads == [payload]


def test_feishu_actions_with_async_container_triggers_handle_action():
    container = AsyncContainer()
    app = create_app(container=container)
    client = TestClient(app)

    payload = {'action': {'value': 'clicked-async'}}
    response = client.post('/webhooks/feishu/actions', json=payload)

    assert response.status_code == 200
    assert response.json() == {}
    assert container.action_payloads == [payload]


def test_internal_run_digest_with_container_triggers_digest():
    container = FakeContainer()
    app = create_app(container=container)
    client = TestClient(app)

    response = client.post('/internal/run-digest', json={})

    assert response.status_code == 200
    assert response.json() == {'queued': True}
    assert container.digest_called == 1


def test_internal_run_digest_without_body_is_accepted():
    container = FakeContainer()
    app = create_app(container=container)
    client = TestClient(app)

    response = client.post('/internal/run-digest')

    assert response.status_code == 200
    assert response.json() == {'queued': True}
    assert container.digest_called == 1


@pytest.mark.asyncio
async def test_feishu_client_tenant_access_token_is_public_and_cached():
    token_calls = 0

    def handler(request):
        nonlocal token_calls
        if request.url.path == '/open-apis/auth/v3/tenant_access_token/internal/':
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    'code': 0,
                    'tenant_access_token': 'tenant-token-1',
                    'expire': 7200,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = FeishuClient(
            app_id='app-id',
            app_secret='app-secret',
            chat_id='chat-id',
            http_client=http_client,
        )
        token_1 = await client.tenant_access_token()
        token_2 = await client.tenant_access_token()

    assert token_1 == 'tenant-token-1'
    assert token_2 == 'tenant-token-1'
    assert token_calls == 1


@pytest.mark.asyncio
async def test_feishu_client_send_text_send_post_and_reply_text_request_contract():
    create_message = _AsyncMethod(
        lambda request, option: im_v1.CreateMessageResponse(
            {'code': 0, 'msg': 'success', 'data': {'message_id': 'om-created'}}
        )
    )
    oapi_client = _Namespace(
        im=_Namespace(
            v1=_Namespace(
                message=_Namespace(acreate=create_message),
            )
        )
    )
    client = FeishuClient(
        app_id='app-id',
        app_secret='app-secret',
        chat_id='chat-main',
        oapi_client=oapi_client,
    )

    await client.send_text('# 🚀 Weekly Digest')
    await client.send_post('🚀 Weekly Digest｜24h', '> hello\n\n1. **repo**')
    await client.reply_text('chat-reply', 'hello')

    assert len(create_message.calls) == 3

    send_text_request = create_message.calls[0][0]
    assert send_text_request.receive_id_type == 'chat_id'
    assert send_text_request.request_body.receive_id == 'chat-main'
    assert send_text_request.request_body.msg_type == 'text'
    assert send_text_request.request_body.content == json.dumps(
        {'text': '# 🚀 Weekly Digest'},
        ensure_ascii=False,
    )

    send_post_request = create_message.calls[1][0]
    assert send_post_request.receive_id_type == 'chat_id'
    assert send_post_request.request_body.receive_id == 'chat-main'
    assert send_post_request.request_body.msg_type == 'post'
    assert send_post_request.request_body.content == json.dumps(
        {
            'zh_cn': {
                'title': '🚀 Weekly Digest｜24h',
                'content': [[{'tag': 'md', 'text': '> hello\n\n1. **repo**'}]],
            }
        },
        ensure_ascii=False,
    )

    reply_text_request = create_message.calls[2][0]
    assert reply_text_request.receive_id_type == 'chat_id'
    assert reply_text_request.request_body.receive_id == 'chat-reply'
    assert reply_text_request.request_body.msg_type == 'text'
    assert reply_text_request.request_body.content == json.dumps(
        {'text': 'hello'},
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_feishu_client_add_and_remove_reaction_request_contract():
    create_reaction = _AsyncMethod(
        lambda request, option: im_v1.CreateMessageReactionResponse(
            {'code': 0, 'msg': 'success', 'data': {'reaction_id': 'reaction-1'}}
        )
    )
    delete_reaction = _AsyncMethod(
        lambda request, option: im_v1.DeleteMessageReactionResponse(
            {'code': 0, 'msg': 'success'}
        )
    )
    oapi_client = _Namespace(
        im=_Namespace(
            v1=_Namespace(
                message_reaction=_Namespace(
                    acreate=create_reaction,
                    adelete=delete_reaction,
                ),
            )
        )
    )
    client = FeishuClient(
        app_id='app-id',
        app_secret='app-secret',
        chat_id='chat-main',
        oapi_client=oapi_client,
    )

    await client.add_reaction('om-message-1', 'Get')
    await client.remove_reaction('om-message-1', 'reaction-1')

    add_request = create_reaction.calls[0][0]
    assert add_request.message_id == 'om-message-1'
    assert add_request.request_body.reaction_type.emoji_type == 'Get'

    remove_request = delete_reaction.calls[0][0]
    assert remove_request.message_id == 'om-message-1'
    assert remove_request.reaction_id == 'reaction-1'


@pytest.mark.asyncio
async def test_feishu_client_send_card_raises_on_feishu_business_error():
    create_message = _AsyncMethod(
        lambda request, option: im_v1.CreateMessageResponse(
            {'code': 999, 'msg': 'invalid payload'}
        )
    )
    client = FeishuClient(
        app_id='app-id',
        app_secret='app-secret',
        chat_id='chat-main',
        oapi_client=_Namespace(
            im=_Namespace(v1=_Namespace(message=_Namespace(acreate=create_message)))
        ),
    )
    with pytest.raises(RuntimeError):
        await client.send_card({'header': {'title': 'bad'}})


@pytest.mark.asyncio
async def test_feishu_client_reply_text_raises_on_feishu_business_error():
    create_message = _AsyncMethod(
        lambda request, option: im_v1.CreateMessageResponse(
            {'code': 19021, 'msg': 'chat not found'}
        )
    )
    client = FeishuClient(
        app_id='app-id',
        app_secret='app-secret',
        chat_id='chat-main',
        oapi_client=_Namespace(
            im=_Namespace(v1=_Namespace(message=_Namespace(acreate=create_message)))
        ),
    )
    with pytest.raises(RuntimeError):
        await client.reply_text('chat-reply', 'hello')


@pytest.mark.asyncio
async def test_feishu_client_concurrent_tenant_access_token_refreshes_once():
    token_calls = 0

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal token_calls
            if request.url.path == '/open-apis/auth/v3/tenant_access_token/internal/':
                token_calls += 1
                await asyncio.sleep(0.05)
                return httpx.Response(
                    200,
                    json={'code': 0, 'tenant_access_token': 'token-concurrent', 'expire': 7200},
                )
            return httpx.Response(404)

    async with httpx.AsyncClient(transport=SlowTransport()) as http_client:
        client = FeishuClient(
            app_id='app-id',
            app_secret='app-secret',
            chat_id='chat-id',
            http_client=http_client,
        )
        token_1, token_2 = await asyncio.gather(
            client.tenant_access_token(),
            client.tenant_access_token(),
        )

    assert token_1 == 'token-concurrent'
    assert token_2 == 'token-concurrent'
    assert token_calls == 1
