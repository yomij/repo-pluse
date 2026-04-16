import json
import logging

import pytest

from repo_pulse.research.base import ResearchRequest
from repo_pulse.research.evidence import RepositoryEvidence
from repo_pulse.research.openai_provider import OpenAIResearchProvider
from repo_pulse.research.prompts import build_research_prompt


class _FakeResponse:
    def __init__(self, output_text, dump_payload):
        self.output_text = output_text
        self._dump_payload = dump_payload

    def model_dump(self):
        return self._dump_payload


class _FakeResponseWithoutModelDump:
    def __init__(self, output_text):
        self.output_text = output_text


class _FakeResponsesAPI:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.responses = _FakeResponsesAPI(response)


def _valid_payload(**overrides):
    payload = {
        "what_it_is": "An AI agent framework",
        "why_now": "Rapid adoption in 2026",
        "fit_for": "Application platform teams",
        "not_for": "Teams needing deterministic offline inference only",
        "trial_verdict": "can_run_locally",
        "trial_requirements": [
            {
                "label": "Python 3.11+",
                "detail": "The examples assume a modern Python runtime.",
                "source": "README quickstart",
            }
        ],
        "trial_time_estimate": "10-15 minutes",
        "quickstart_steps": [
            {
                "label": "Install dependencies",
                "action": "Run `uv sync` in the repository root.",
                "expected_result": "Dependencies install without errors.",
                "source": "README quickstart",
            }
        ],
        "success_signal": "The sample command prints a successful agent response.",
        "common_blockers": [
            {
                "label": "Missing API key",
                "detail": "The default example needs an API key in the environment.",
                "source": "Docs authentication section",
            }
        ],
        "best_practices": ["Pin model versions", "Track eval metrics"],
        "risks": ["Breaking API changes"],
        "citations": [
            {
                "title": "OpenAI Python",
                "url": "https://github.com/openai/openai-python",
                "snippet": "SDK release notes",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _request(research_run_id: str, *, evidence=None) -> ResearchRequest:
    return ResearchRequest(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id=research_run_id,
        evidence=evidence,
    )


def test_build_research_prompt_requires_onboarding_schema():
    prompt = build_research_prompt(_request("run-prompt"))

    assert "quickstart 已移除，必须改用 quickstart_steps" in prompt
    assert "最短且现实可行的首次运行路径" in prompt
    assert "仅在仓库材料或权威公开来源明确支持时才能写出具体命令" in prompt
    assert "不能确认命令时，必须明确写“信息不足以确认”" in prompt
    assert "阻塞项必须放入 common_blockers，不要埋在 risks" in prompt
    assert "优先参考官方文档与仓库作者提供的示例" in prompt
    assert '"trial_requirements": [' in prompt
    assert '"common_blockers": [' in prompt
    assert '"quickstart_steps": [' in prompt
    assert '"label": "字符串"' in prompt
    assert '"detail": "字符串"' in prompt
    assert '"action": "字符串"' in prompt
    assert '"expected_result": "字符串"' in prompt
    assert "trial_verdict、quickstart_steps、success_signal 三者必须相互一致" in prompt
    assert '"quickstart":' not in prompt


@pytest.mark.asyncio
async def test_openai_provider_calls_responses_api_with_web_search_and_reasoning(caplog):
    caplog.set_level(logging.INFO)
    payload = _valid_payload(
        why_now="Ecosystem momentum",
        fit_for="Platform teams",
        not_for="Strongly regulated offline environments",
        best_practices=["Use evals"],
        risks=["Model behavior drift"],
        citations=[],
    )
    response = _FakeResponse(json.dumps(payload), {"output": []})
    client = _FakeClient(response)
    provider = OpenAIResearchProvider(client=client, model="gpt-5", reasoning_effort="high")

    request = _request("run-openai-1")
    await provider.research(request)
    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]

    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["tools"] == [{"type": "web_search"}]
    assert call["include"] == ["web_search_call.action.sources"]
    assert call["reasoning"] == {"effort": "high"}
    assert "公开网络资料" in call["input"]
    assert "优先官方仓库页和官方文档" in call["input"]
    assert "严格 JSON" in call["input"]
    assert "中文工程团队" in call["input"]
    assert "英文术语" in call["input"]
    assert "fit_for" in call["input"]
    assert "risks" in call["input"]
    assert "最短且现实可行的首次运行路径" in call["input"]
    assert "仅在仓库材料或权威公开来源明确支持时才能写出具体命令" in call["input"]
    assert "不能确认命令时，必须明确写“信息不足以确认”" in call["input"]
    assert "阻塞项必须放入 common_blockers，不要埋在 risks" in call["input"]
    assert "优先参考官方文档与仓库作者提供的示例" in call["input"]
    assert "quickstart 已移除，必须改用 quickstart_steps" in call["input"]
    assert "trial_verdict、quickstart_steps、success_signal 三者必须相互一致" in call["input"]
    assert any(
        payload["event"] == "research.started"
        and payload["provider"] == "openai"
        and payload["model"] == "gpt-5"
        and payload["research_run_id"] == "run-openai-1"
        for payload in payloads
    )
    progress_payloads = [
        payload
        for payload in payloads
        if payload["event"] == "research.progress" and payload["research_run_id"] == "run-openai-1"
    ]
    assert {payload["stage"] for payload in progress_payloads} == {
        "openai_response",
        "payload_validation",
    }
    assert all(isinstance(payload["elapsed_ms"], int) for payload in progress_payloads)
    assert any(
        payload["event"] == "research.completed"
        and payload["research_run_id"] == "run-openai-1"
        and payload["citations_count"] == 0
        and payload["best_practices_count"] == 1
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_openai_provider_includes_repository_evidence_in_prompt():
    payload = _valid_payload(
        why_now="Ecosystem momentum",
        fit_for="Platform teams",
        not_for="Offline-only environments",
        best_practices=["Use evals"],
        risks=["Model behavior drift"],
        citations=[],
    )
    response = _FakeResponse(json.dumps(payload), {"output": []})
    client = _FakeClient(response)
    provider = OpenAIResearchProvider(client=client, model="gpt-5", reasoning_effort="high")

    request = _request(
        "run-openai-evidence",
        evidence=RepositoryEvidence(
            full_name="acme/agent",
            repo_url="https://github.com/acme/agent",
            description="Agent runtime",
            homepage="https://agent.acme.dev",
            language="Python",
            default_branch="main",
            topics=["ai", "agents"],
            readme_excerpt="Run demo first.",
            releases=["v1.2.0: Stability fixes"],
            recent_commits=["ship eval dashboard"],
            key_paths=["docs", "examples", "src"],
        ),
    )
    await provider.research(request)

    prompt = client.responses.calls[0]["input"]
    assert "仓库一手证据" in prompt
    assert "Run demo first." in prompt
    assert "ship eval dashboard" in prompt
    assert "优先使用 evidence 中的一手资料" in prompt


@pytest.mark.asyncio
async def test_openai_provider_parses_output_text_citations():
    payload = _valid_payload()
    response = _FakeResponse(json.dumps(payload), {"output": []})
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-2")
    result = await provider.research(request)

    assert result.what_it_is == "An AI agent framework"
    assert result.why_now == "Rapid adoption in 2026"
    assert result.fit_for == "Application platform teams"
    assert result.not_for == "Teams needing deterministic offline inference only"
    assert result.trial_verdict == "can_run_locally"
    assert result.trial_requirements[0].label == "Python 3.11+"
    assert result.trial_requirements[0].detail == "The examples assume a modern Python runtime."
    assert result.trial_requirements[0].source == "README quickstart"
    assert result.trial_time_estimate == "10-15 minutes"
    assert result.quickstart_steps[0].label == "Install dependencies"
    assert result.quickstart_steps[0].action == "Run `uv sync` in the repository root."
    assert result.quickstart_steps[0].expected_result == "Dependencies install without errors."
    assert result.quickstart_steps[0].source == "README quickstart"
    assert result.success_signal == "The sample command prints a successful agent response."
    assert result.common_blockers[0].label == "Missing API key"
    assert result.common_blockers[0].detail == "The default example needs an API key in the environment."
    assert result.common_blockers[0].source == "Docs authentication section"
    assert result.best_practices == ["Pin model versions", "Track eval metrics"]
    assert result.risks == ["Breaking API changes"]
    assert len(result.citations) == 1
    assert result.citations[0].title == "OpenAI Python"
    assert result.citations[0].url == "https://github.com/openai/openai-python"
    assert result.citations[0].snippet == "SDK release notes"
    assert result.metadata["provider"] == "openai"
    assert result.metadata["model"] == "gpt-5"
    assert result.metadata["batch_id"]


@pytest.mark.asyncio
async def test_openai_provider_falls_back_to_web_search_sources_when_missing_citations():
    payload = _valid_payload(
        why_now="Research momentum is high",
        fit_for="Developers evaluating agent stacks",
        not_for="Air-gapped production systems",
        best_practices=["Prefer official docs"],
        risks=["Documentation may lag behind releases"],
    )
    payload.pop("citations")
    dump_payload = {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {
                            "title": "OpenAI Docs",
                            "url": "https://platform.openai.com/docs",
                            "snippet": "Official docs",
                        }
                    ]
                },
            }
        ]
    }
    response = _FakeResponse(json.dumps(payload), dump_payload)
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-3")
    result = await provider.research(request)

    assert result.what_it_is == "An AI agent framework"
    assert len(result.citations) == 1
    assert result.citations[0].title == "OpenAI Docs"
    assert result.citations[0].url == "https://platform.openai.com/docs"
    assert result.citations[0].snippet == "Official docs"


@pytest.mark.asyncio
async def test_openai_provider_raises_value_error_for_invalid_json():
    response = _FakeResponse("not-json", {"output": []})
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-4")

    with pytest.raises(ValueError):
        await provider.research(request)


@pytest.mark.asyncio
async def test_openai_provider_rejects_legacy_quickstart_field():
    payload = _valid_payload(quickstart="Read docs")
    response = _FakeResponse(json.dumps(payload), {"output": []})
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-5")

    with pytest.raises(ValueError, match="payload.quickstart is no longer supported"):
        await provider.research(request)


@pytest.mark.asyncio
async def test_openai_provider_propagates_parser_semantic_validation_errors():
    payload = _valid_payload(trial_verdict="can_run_locally", quickstart_steps=[])
    response = _FakeResponse(json.dumps(payload), {"output": []})
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-semantic-error")

    with pytest.raises(ValueError, match="quickstart_steps must not be empty"):
        await provider.research(request)


@pytest.mark.asyncio
async def test_openai_provider_requires_concrete_success_signal_for_local_trials():
    payload = _valid_payload(
        trial_verdict="can_run_locally",
        success_signal="信息不足以确认",
    )
    response = _FakeResponse(json.dumps(payload), {"output": []})
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-semantic-success-signal")

    with pytest.raises(ValueError, match="success_signal must be concrete"):
        await provider.research(request)


@pytest.mark.asyncio
async def test_openai_provider_falls_back_when_citations_is_not_list():
    payload = _valid_payload(
        why_now="Research momentum is high",
        fit_for="Developers evaluating agent stacks",
        not_for="Air-gapped production systems",
        best_practices=["Prefer official docs"],
        risks=["Documentation may lag behind releases"],
        citations="invalid-citations",
    )
    dump_payload = {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {
                            "title": "Fallback Source",
                            "url": "https://example.com/fallback",
                            "snippet": "fallback snippet",
                        }
                    ]
                },
            }
        ]
    }
    response = _FakeResponse(json.dumps(payload), dump_payload)
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-6")
    result = await provider.research(request)

    assert len(result.citations) == 1
    assert result.citations[0].url == "https://example.com/fallback"


@pytest.mark.asyncio
async def test_openai_provider_handles_missing_model_dump_without_crashing():
    payload = _valid_payload(
        why_now="Research momentum is high",
        fit_for="Developers evaluating agent stacks",
        not_for="Air-gapped production systems",
        best_practices=["Prefer official docs"],
        risks=["Documentation may lag behind releases"],
    )
    payload.pop("citations")
    response = _FakeResponseWithoutModelDump(json.dumps(payload))
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-7")
    result = await provider.research(request)

    assert result.what_it_is == "An AI agent framework"
    assert result.citations == []


@pytest.mark.asyncio
async def test_openai_provider_ignores_non_web_search_sources_in_fallback():
    payload = _valid_payload(
        why_now="Research momentum is high",
        fit_for="Developers evaluating agent stacks",
        not_for="Air-gapped production systems",
        best_practices=["Prefer official docs"],
        risks=["Documentation may lag behind releases"],
        citations=None,
    )
    dump_payload = {
        "output": [
            {
                "type": "tool_call",
                "action": {
                    "sources": [
                        {
                            "title": "Should Be Ignored",
                            "url": "https://example.com/ignored",
                        }
                    ]
                },
            },
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {
                            "title": "Kept",
                            "url": "https://example.com/kept",
                        }
                    ]
                },
            },
        ]
    }
    response = _FakeResponse(json.dumps(payload), dump_payload)
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-8")
    result = await provider.research(request)

    assert len(result.citations) == 1
    assert result.citations[0].url == "https://example.com/kept"


@pytest.mark.asyncio
async def test_openai_provider_falls_back_when_citations_is_empty_list():
    payload = _valid_payload(
        why_now="Research momentum is high",
        fit_for="Developers evaluating agent stacks",
        not_for="Air-gapped production systems",
        best_practices=["Prefer official docs"],
        risks=["Documentation may lag behind releases"],
        citations=[],
    )
    dump_payload = {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {
                            "title": "Fallback Source",
                            "url": "https://example.com/empty-list-fallback",
                            "snippet": "fallback from empty list",
                        }
                    ]
                },
            }
        ]
    }
    response = _FakeResponse(json.dumps(payload), dump_payload)
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-9")
    result = await provider.research(request)

    assert len(result.citations) == 1
    assert result.citations[0].url == "https://example.com/empty-list-fallback"


@pytest.mark.asyncio
async def test_openai_provider_dedupes_citations_by_url_preserving_first_occurrence():
    payload = _valid_payload(
        why_now="Research momentum is high",
        fit_for="Developers evaluating agent stacks",
        not_for="Air-gapped production systems",
        best_practices=["Prefer official docs"],
        risks=["Documentation may lag behind releases"],
        citations=[
            {
                "title": "First Title",
                "url": "https://example.com/dup",
                "snippet": "first snippet",
            },
            {
                "title": "Second Title",
                "url": "https://example.com/dup",
                "snippet": "second snippet",
            },
        ],
    )
    response = _FakeResponse(json.dumps(payload), {"output": []})
    provider = OpenAIResearchProvider(client=_FakeClient(response))

    request = _request("run-openai-10")
    result = await provider.research(request)

    assert len(result.citations) == 1
    assert result.citations[0].title == "First Title"
    assert result.citations[0].snippet == "first snippet"


@pytest.mark.asyncio
async def test_openai_provider_logs_failure_with_exception_type(caplog):
    caplog.set_level(logging.INFO)
    provider = OpenAIResearchProvider(client=_FakeClient(RuntimeError("responses boom")))

    request = _request("run-openai-fail")

    with pytest.raises(RuntimeError, match="responses boom"):
        await provider.research(request)

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert any(
        payload["event"] == "research.failed"
        and payload["research_run_id"] == "run-openai-fail"
        and payload["exception_type"] == "RuntimeError"
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )
