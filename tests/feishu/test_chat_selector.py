import httpx
import pytest
import respx

from repo_pulse.feishu.client import FeishuChat, FeishuClient
from repo_pulse.feishu.chat_selector import run_select_chat_id_command


@respx.mock
@pytest.mark.asyncio
async def test_feishu_client_list_chats_fetches_all_pages():
    respx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
        )
    )
    first_page = respx.get("https://open.feishu.cn/open-apis/im/v1/chats").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "chat_id": "oc_chat_1",
                                "name": "Repo Pulse Alpha",
                                "description": "alpha",
                                "external": False,
                            }
                        ],
                        "has_more": True,
                        "page_token": "next-page",
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "chat_id": "oc_chat_2",
                                "name": "Repo Pulse Beta",
                                "description": "beta",
                                "external": True,
                            }
                        ],
                        "has_more": False,
                    },
                },
            ),
        ]
    )

    client = FeishuClient(app_id="app-id", app_secret="app-secret", chat_id="")
    chats = await client.list_chats()
    await client.close()

    assert first_page.call_count == 2
    assert chats == [
        FeishuChat(
            chat_id="oc_chat_1",
            name="Repo Pulse Alpha",
            description="alpha",
            external=False,
        ),
        FeishuChat(
            chat_id="oc_chat_2",
            name="Repo Pulse Beta",
            description="beta",
            external=True,
        ),
    ]


@respx.mock
@pytest.mark.asyncio
async def test_feishu_client_list_chats_surfaces_feishu_permission_error():
    respx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
        )
    )
    respx.get("https://open.feishu.cn/open-apis/im/v1/chats").mock(
        return_value=httpx.Response(
            400,
            json={
                "code": 99991672,
                "msg": "Access denied. One of the following scopes is required: [im:chat:readonly]",
            },
        )
    )

    client = FeishuClient(app_id="app-id", app_secret="app-secret", chat_id="")
    with pytest.raises(RuntimeError) as exc_info:
        await client.list_chats()
    await client.close()

    assert "code=99991672" in str(exc_info.value)
    assert "im:chat:readonly" in str(exc_info.value)


def test_run_select_chat_id_command_writes_selected_chat_id(tmp_path, monkeypatch, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "FEISHU_APP_ID=app-id",
                "FEISHU_APP_SECRET=app-secret",
                "FEISHU_CHAT_IDS=",
                "FEISHU_CHAT_ID=",
                "DIGEST_TOP_K=10",
                "",
            ]
        )
    )

    async def _fake_fetch_chats(path):
        assert path == env_path
        return [
            FeishuChat(chat_id="oc_chat_1", name="Repo Pulse Alpha"),
            FeishuChat(chat_id="oc_chat_2", name="Repo Pulse Beta"),
        ]

    monkeypatch.setattr(
        "repo_pulse.feishu.chat_selector.fetch_chats_for_selection",
        _fake_fetch_chats,
    )

    exit_code = run_select_chat_id_command(
        name_filter="repo",
        env_path=env_path,
        input_func=lambda _: "2",
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Repo Pulse Beta" in captured.out
    env_content = env_path.read_text()
    assert "FEISHU_CHAT_IDS=oc_chat_2" in env_content
    assert "FEISHU_CHAT_ID=oc_chat_2" in env_content
    assert "DIGEST_TOP_K=10" in env_content


def test_run_select_chat_id_command_returns_one_when_no_chat_matches(tmp_path, monkeypatch, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "FEISHU_APP_ID=app-id",
                "FEISHU_APP_SECRET=app-secret",
                "FEISHU_CHAT_IDS=oc_existing",
                "FEISHU_CHAT_ID=oc_old",
                "",
            ]
        )
    )

    async def _fake_fetch_chats(path):
        assert path == env_path
        return [FeishuChat(chat_id="oc_chat_1", name="Other Group")]

    monkeypatch.setattr(
        "repo_pulse.feishu.chat_selector.fetch_chats_for_selection",
        _fake_fetch_chats,
    )

    exit_code = run_select_chat_id_command(
        name_filter="repo",
        env_path=env_path,
        input_func=lambda _: "1",
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "No Feishu chats matched" in captured.out
    env_content = env_path.read_text()
    assert "FEISHU_CHAT_IDS=oc_existing" in env_content
    assert "FEISHU_CHAT_ID=oc_old" in env_content
