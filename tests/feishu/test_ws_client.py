import json
from types import SimpleNamespace

import pytest


class _FakeContainer:
    def __init__(self):
        self.payloads = []

    async def handle_event(self, payload):
        self.payloads.append(payload)


def _receive_event(content, message_type="text"):
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id="om-user-message",
                chat_id="oc-chat",
                message_type=message_type,
                content=content,
            )
        )
    )


@pytest.mark.asyncio
async def test_long_connection_message_event_is_adapted_to_runtime_payload():
    from repo_pulse.feishu.ws_client import FeishuLongConnectionClient

    container = _FakeContainer()
    client = FeishuLongConnectionClient(
        app_id="cli-id",
        app_secret="secret",
        container=container,
        ws_client_factory=lambda **kwargs: SimpleNamespace(start=lambda: None),
    )

    await client.handle_message_event(
        _receive_event(json.dumps({"text": "/a openai/openai-python"}))
    )

    assert container.payloads == [
        {
            "event": {
                "chat_id": "oc-chat",
                "message": {
                    "message_id": "om-user-message",
                    "text": "/a openai/openai-python",
                },
            }
        }
    ]


@pytest.mark.asyncio
async def test_long_connection_message_event_ignores_non_text_messages():
    from repo_pulse.feishu.ws_client import FeishuLongConnectionClient

    container = _FakeContainer()
    client = FeishuLongConnectionClient(
        app_id="cli-id",
        app_secret="secret",
        container=container,
        ws_client_factory=lambda **kwargs: SimpleNamespace(start=lambda: None),
    )

    await client.handle_message_event(
        _receive_event(json.dumps({"image_key": "img-key"}), message_type="image")
    )

    assert container.payloads == []


def test_long_connection_start_registers_receive_message_handler():
    from repo_pulse.feishu.ws_client import FeishuLongConnectionClient

    registered = {}
    built_handlers = []
    started = []

    class _FakeBuilder:
        def __init__(self, encrypt_key, verification_token):
            registered["encrypt_key"] = encrypt_key
            registered["verification_token"] = verification_token

        def register_p2_im_message_receive_v1(self, handler):
            registered["handler"] = handler
            return self

        def build(self):
            built_handlers.append(self)
            return "dispatcher"

    def _fake_ws_client_factory(**kwargs):
        registered["ws_kwargs"] = kwargs
        return SimpleNamespace(start=lambda: started.append(True))

    client = FeishuLongConnectionClient(
        app_id="cli-id",
        app_secret="secret",
        container=_FakeContainer(),
        encrypt_key="encrypt",
        verification_token="verify",
        event_handler_builder_factory=_FakeBuilder,
        ws_client_factory=_fake_ws_client_factory,
    )

    client.start()

    assert callable(registered["handler"])
    assert registered["encrypt_key"] == "encrypt"
    assert registered["verification_token"] == "verify"
    assert registered["ws_kwargs"]["app_id"] == "cli-id"
    assert registered["ws_kwargs"]["app_secret"] == "secret"
    assert registered["ws_kwargs"]["event_handler"] == "dispatcher"
    assert started == [True]


def test_long_connection_real_client_mode_defers_sdk_client_creation_to_thread():
    from repo_pulse.feishu.ws_client import FeishuLongConnectionClient

    targets = []

    class _FakeThread:
        def __init__(self, target, name, daemon):
            targets.append((target, name, daemon))

        def start(self):
            pass

    client = FeishuLongConnectionClient(
        app_id="cli-id",
        app_secret="secret",
        container=_FakeContainer(),
        thread_factory=_FakeThread,
    )

    client.start()

    assert client._client is None
    assert len(targets) == 1
    assert targets[0][1] == "feishu-long-connection"
    assert targets[0][2] is True
