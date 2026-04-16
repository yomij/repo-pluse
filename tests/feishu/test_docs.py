import json
import asyncio

import lark_oapi.api.docx.v1 as docx_v1
import lark_oapi.api.drive.v1 as drive_v1
import pytest
import httpx

from repo_pulse.feishu.docs import FeishuDocsClient
from repo_pulse.research.base import (
    OnboardingFact,
    QuickstartStep,
    ResearchResult,
    TRIAL_VERDICT_NEEDS_API_KEY,
)


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


@pytest.mark.asyncio
async def test_feishu_docs_client_creates_document_and_appends_blocks():
    create_document = _AsyncMethod(
        lambda request, option: docx_v1.CreateDocumentResponse(
            {"code": 0, "msg": "success", "data": {"document": {"document_id": "doc-123"}}}
        )
    )
    create_children = _AsyncMethod(
        lambda request, option: docx_v1.CreateDocumentBlockChildrenResponse(
            {"code": 0, "msg": "success", "data": {"children_id": ["blk-1"]}}
        )
    )
    patch_public = _AsyncMethod(
        lambda request, option: drive_v1.PatchPermissionPublicResponse(
            {"code": 0, "msg": "success", "data": {"permission_public": {"link_share_entity": "tenant_editable"}}}
        )
    )
    client = FeishuDocsClient(
        app_id="app-id",
        app_secret="app-secret",
        folder_token="fld-001",
        oapi_client=_Namespace(
            docx=_Namespace(
                v1=_Namespace(
                    document=_Namespace(acreate=create_document),
                    document_block_children=_Namespace(acreate=create_children),
                )
            ),
            drive=_Namespace(
                v1=_Namespace(
                    permission_public=_Namespace(apatch=patch_public),
                )
            ),
        ),
    )
    doc_url = await client.upsert_project_doc(
        "acme/agent",
        "# acme/agent 项目详情\n\n## 它是什么\n一个项目。\n\n- [README](https://github.com/acme/agent)\n",
    )

    assert doc_url == "https://feishu.cn/docx/doc-123"

    create_document_request = create_document.calls[0][0]
    assert create_document_request.request_body.folder_token == "fld-001"
    assert create_document_request.request_body.title == "acme/agent 项目详情"

    create_blocks_request = create_children.calls[0][0]
    assert create_blocks_request.document_id == "doc-123"
    assert create_blocks_request.block_id == "doc-123"
    payload = create_blocks_request.request_body
    assert payload.index == 0
    assert payload.children[0].block_type == 3
    assert payload.children[0].heading1.elements[0].text_run.content == "acme/agent 项目详情"
    assert payload.children[1].block_type == 4
    assert payload.children[2].block_type == 2
    assert payload.children[3].block_type == 12
    link_style = payload.children[3].bullet.elements[0].text_run.text_element_style
    assert "https%3A%2F%2Fgithub.com%2Facme%2Fagent" in link_style.link.url
    patch_public_request = patch_public.calls[0][0]
    assert patch_public_request.type == "docx"
    assert patch_public_request.token == "doc-123"
    assert patch_public_request.request_body.link_share_entity == "tenant_editable"


@pytest.mark.asyncio
async def test_feishu_docs_client_reuses_document_and_replaces_children_on_second_upsert():
    create_document = _AsyncMethod(
        lambda request, option: docx_v1.CreateDocumentResponse(
            {"code": 0, "msg": "success", "data": {"document": {"document_id": "doc-123"}}}
        )
    )
    get_children = _AsyncMethod(
        lambda request, option: docx_v1.GetDocumentBlockChildrenResponse(
            {
                "code": 0,
                "msg": "success",
                "data": {"items": [{"block_id": "blk-1"}, {"block_id": "blk-2"}], "has_more": False},
            }
        )
    )
    delete_children = _AsyncMethod(
        lambda request, option: docx_v1.BatchDeleteDocumentBlockChildrenResponse(
            {"code": 0, "msg": "success", "data": {"revision_id": 2}}
        )
    )
    create_children = _AsyncMethod(
        lambda request, option: docx_v1.CreateDocumentBlockChildrenResponse(
            {"code": 0, "msg": "success", "data": {"children_id": ["blk-3"]}}
        )
    )
    patch_public = _AsyncMethod(
        lambda request, option: drive_v1.PatchPermissionPublicResponse(
            {"code": 0, "msg": "success", "data": {"permission_public": {"link_share_entity": "tenant_editable"}}}
        )
    )
    client = FeishuDocsClient(
        app_id="app-id",
        app_secret="app-secret",
        oapi_client=_Namespace(
            docx=_Namespace(
                v1=_Namespace(
                    document=_Namespace(acreate=create_document),
                    document_block_children=_Namespace(
                        aget=get_children,
                        abatch_delete=delete_children,
                        acreate=create_children,
                    ),
                )
            ),
            drive=_Namespace(
                v1=_Namespace(
                    permission_public=_Namespace(apatch=patch_public),
                )
            ),
        ),
    )
    first_url = await client.upsert_project_doc("acme/agent", "# 标题\n\n第一版")
    second_url = await client.upsert_project_doc("acme/agent", "# 标题\n\n第二版")

    assert first_url == "https://feishu.cn/docx/doc-123"
    assert second_url == "https://feishu.cn/docx/doc-123"
    assert len(create_document.calls) == 1

    delete_request = delete_children.calls[0][0]
    assert delete_request.document_id == "doc-123"
    assert delete_request.block_id == "doc-123"
    assert delete_request.request_body.start_index == 0
    assert delete_request.request_body.end_index == 2

    assert len(create_children.calls) == 2
    second_create_payload = create_children.calls[1][0].request_body
    assert second_create_payload.children[1].text.elements[0].text_run.content == "第二版"
    assert len(patch_public.calls) == 2


@pytest.mark.asyncio
async def test_feishu_docs_client_reuses_existing_doc_url_without_creating_new_document():
    get_children = _AsyncMethod(
        lambda request, option: docx_v1.GetDocumentBlockChildrenResponse(
            {
                "code": 0,
                "msg": "success",
                "data": {"items": [{"block_id": "blk-1"}], "has_more": False},
            }
        )
    )
    delete_children = _AsyncMethod(
        lambda request, option: docx_v1.BatchDeleteDocumentBlockChildrenResponse(
            {"code": 0, "msg": "success", "data": {"revision_id": 2}}
        )
    )
    create_children = _AsyncMethod(
        lambda request, option: docx_v1.CreateDocumentBlockChildrenResponse(
            {"code": 0, "msg": "success", "data": {"children_id": ["blk-3"]}}
        )
    )
    create_document = _AsyncMethod(
        lambda request, option: docx_v1.CreateDocumentResponse(
            {"code": 0, "msg": "success", "data": {"document": {"document_id": "new-doc"}}}
        )
    )
    patch_public = _AsyncMethod(
        lambda request, option: drive_v1.PatchPermissionPublicResponse(
            {"code": 0, "msg": "success", "data": {"permission_public": {"link_share_entity": "tenant_editable"}}}
        )
    )
    client = FeishuDocsClient(
        app_id="app-id",
        app_secret="app-secret",
        oapi_client=_Namespace(
            docx=_Namespace(
                v1=_Namespace(
                    document=_Namespace(acreate=create_document),
                    document_block_children=_Namespace(
                        aget=get_children,
                        abatch_delete=delete_children,
                        acreate=create_children,
                    ),
                )
            ),
            drive=_Namespace(
                v1=_Namespace(
                    permission_public=_Namespace(apatch=patch_public),
                )
            ),
        ),
    )

    doc_url = await client.upsert_project_doc(
        "acme/agent",
        "# 标题\n\n第二版",
        existing_doc_url="https://feishu.cn/docx/doc-legacy",
    )

    assert doc_url == "https://feishu.cn/docx/doc-legacy"
    assert len(create_document.calls) == 0
    assert delete_children.calls[0][0].document_id == "doc-legacy"
    assert patch_public.calls[0][0].request_body.link_share_entity == "tenant_editable"


@pytest.mark.asyncio
async def test_feishu_docs_client_concurrent_tenant_access_token_refreshes_once():
    token_calls = 0

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal token_calls
            if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal/":
                token_calls += 1
                await asyncio.sleep(0.05)
                return httpx.Response(
                    200,
                    json={"code": 0, "tenant_access_token": "docs-token", "expire": 7200},
                )
            return httpx.Response(404)

    async with httpx.AsyncClient(transport=SlowTransport()) as http_client:
        client = FeishuDocsClient(
            app_id="app-id",
            app_secret="app-secret",
            http_client=http_client,
        )
        token_1, token_2 = await asyncio.gather(
            client.tenant_access_token(),
            client.tenant_access_token(),
        )

    assert token_1 == "docs-token"
    assert token_2 == "docs-token"
    assert token_calls == 1


def test_render_project_markdown_uses_onboarding_first_structure():
    from repo_pulse.feishu.docs import render_project_markdown

    result = ResearchResult(
        what_it_is="这是一个 agent 平台。",
        why_now="社区增长很快。",
        fit_for="平台团队。",
        not_for="完全离线环境。",
        trial_verdict="can_run_locally",
        trial_requirements=[
            OnboardingFact(
                label="Python 3.11+",
                detail="示例运行依赖 Python 环境。",
                source="README / Quick Start",
            )
        ],
        trial_time_estimate="3-10 分钟",
        quickstart_steps=[
            QuickstartStep(
                label="安装依赖",
                action="运行 `uv sync`。",
                expected_result="依赖安装完成。",
                source="README / Quick Start",
            ),
            QuickstartStep(
                label="启动示例",
                action="运行 `uv run python examples/demo.py`。",
                expected_result="终端输出 successful response。",
                source="README / Quick Start",
            ),
        ],
        success_signal="示例命令输出 successful response。",
        common_blockers=[
            OnboardingFact(
                label="缺少 API Key",
                detail="未设置环境变量会导致示例启动失败。",
                source="README / Troubleshooting",
            )
        ],
        best_practices=["先跑最小 demo"],
        risks=["依赖外部模型接口"],
    )

    markdown = render_project_markdown("acme/agent", result)

    assert "## 项目简介" in markdown
    assert "## 为什么最近火" in markdown
    assert "## 是否适合我" in markdown
    assert "## 是否能快速试玩" in markdown
    assert "## 最短体验路径" in markdown
    assert "## 前置条件与外部依赖" in markdown
    assert "## 常见阻塞与失败信号" in markdown
    assert "## 最佳实践" in markdown
    assert "## 局限与风险" in markdown
    assert "## 参考资料与引用链接" in markdown
    assert "## 生成元数据" in markdown
    assert "## 快速上手" not in markdown
    assert "1. **安装依赖**" in markdown
    assert "- 成功信号：示例命令输出 successful response。" in markdown
    assert "- 阻塞：**缺少 API Key**" in markdown


def test_render_project_markdown_maps_trial_verdict_and_time_estimate():
    from repo_pulse.feishu.docs import render_project_markdown

    result = ResearchResult(
        what_it_is="这是一个 agent 平台。",
        why_now="社区增长很快。",
        trial_verdict=TRIAL_VERDICT_NEEDS_API_KEY,
        trial_requirements=[],
        trial_time_estimate="5-15 分钟",
        quickstart_steps=[],
        success_signal="信息不足以确认",
        common_blockers=[],
    )

    markdown = render_project_markdown("acme/agent", result)

    assert "结论：需要 API Key 才能完成试玩（预计耗时：5-15 分钟）" in markdown
    assert "**API Key / 凭证**" in markdown
    assert "准备 API Key / 账号凭证" in markdown
    assert "**缺少 API Key / 凭证**" in markdown


def test_markdown_line_to_block_strips_inline_markdown_markers():
    from repo_pulse.feishu.docs import _markdown_line_to_block

    block = _markdown_line_to_block("1. **安装依赖**：运行 `uv sync`。")

    assert block["block_type"] == 2
    assert block["text"]["elements"][0]["text_run"]["content"] == "1. 安装依赖：运行 uv sync。"
