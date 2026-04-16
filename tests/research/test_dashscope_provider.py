import json
import logging

import pytest
import requests

from repo_pulse.research.base import ResearchRequest
from repo_pulse.research.evidence import RepositoryEvidence


class _Chunk:
    def __init__(self, content="", references=None):
        self.output = {
            "message": {
                "content": content,
                "extra": {
                    "deep_research": {
                        "references": references or [],
                    }
                },
            }
        }


class _StructuredResponse:
    def __init__(self, content):
        self.output = {
            "choices": [
                {
                    "message": {
                        "content": content,
                    }
                }
            ]
        }


class _RaisingStream:
    def __init__(self, chunks, error):
        self._chunks = list(chunks)
        self._error = error

    def __iter__(self):
        for chunk in self._chunks:
            yield chunk
        raise self._error


class _FakeGenerationClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def call(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response


def _structured_payload(**overrides):
    payload = {
        "what_it_is": "AI agent framework",
        "why_now": "社区增长快",
        "fit_for": "适合平台工程团队",
        "not_for": "不适合完全离线环境",
        "trial_verdict": "can_run_locally",
        "trial_requirements": [
            {
                "label": "Python 3.11+",
                "detail": "示例依赖 Python 3.11+ 运行时",
                "source": "README / Setup",
            }
        ],
        "trial_time_estimate": "10-15 分钟",
        "quickstart_steps": [
            {
                "label": "安装依赖",
                "action": "运行 `uv sync` 安装依赖",
                "expected_result": "依赖安装完成且无错误输出",
                "source": "README / Quick Start",
            }
        ],
        "success_signal": "示例命令返回预期响应",
        "common_blockers": [
            {
                "label": "缺少 API key",
                "detail": "默认示例需要环境变量中的 API key",
                "source": "README / Setup",
            }
        ],
        "best_practices": ["先从官方示例开始"],
        "risks": ["依赖外部模型服务"],
        "citations": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_dashscope_provider_researches_directly_without_fake_question_turn():
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    research_client = _FakeGenerationClient(
        responses=[
            [
                _Chunk(
                    "完整研究报告",
                    references=[{"title": "README", "url": "https://github.com/acme/agent"}],
                )
            ]
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            _StructuredResponse(
                json.dumps(
                    _structured_payload(citations=[]),
                    ensure_ascii=False,
                )
            )
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
    )

    await provider.research(
        ResearchRequest(
            full_name="acme/agent",
            repo_url="https://github.com/acme/agent",
            research_run_id="run-direct",
            evidence=RepositoryEvidence(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                readme_excerpt="Run demo first.",
            ),
        )
    )

    assert len(research_client.calls) == 1
    assert research_client.calls[0]["output_format"] == "model_summary_report"
    assert len(research_client.calls[0]["messages"]) == 1
    report_prompt = research_client.calls[0]["messages"][0]["content"]
    assert "Run demo first." in report_prompt
    assert "仓库一手证据" in report_prompt
    assert "不要向用户提问澄清问题" in report_prompt
    assert "重点回答这个仓库能否快速试玩" in report_prompt
    assert "最短体验路径是什么" in report_prompt
    assert "成功信号是什么" in report_prompt
    assert "会先卡在哪里" in report_prompt
    assert "如果命令无法从一手资料确认" in report_prompt
    assert "信息不足以确认" in report_prompt
    assert "不要编造命令" in report_prompt
    assert "优先使用官方仓库、官方文档、README、examples、setup 文件里的证据" in report_prompt
    assert "严格 JSON" not in report_prompt


@pytest.mark.asyncio
async def test_dashscope_provider_calls_deep_research_then_structurer(caplog):
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    caplog.set_level(logging.INFO)
    research_client = _FakeGenerationClient(
        responses=[
            [
                _Chunk("第一段", references=[{"title": "README", "url": "https://github.com/acme/agent"}]),
                _Chunk("第二段"),
            ],
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            _StructuredResponse(
                json.dumps(
                    _structured_payload(
                        trial_verdict="needs_complex_setup",
                        trial_requirements=[
                            {
                                "label": "Docker",
                                "detail": "依赖 docker compose 启动本地服务",
                                "source": "README / Setup",
                            }
                        ],
                        trial_time_estimate="10-20 分钟",
                        quickstart_steps=[
                            {
                                "label": "启动服务栈",
                                "action": "运行 `docker compose up`",
                                "expected_result": "核心服务 healthy 且可访问",
                                "source": "README / Quick Start",
                            }
                        ],
                        success_signal="Web UI 在 localhost 正常响应",
                        common_blockers=[
                            {
                                "label": "端口冲突",
                                "detail": "本地 3000/8080 端口占用会导致启动失败",
                                "source": "README / Troubleshooting",
                            }
                        ],
                        metadata={
                            "source_batch": "structurer-batch",
                            "provider": "from-structurer",
                        },
                        citations=[],
                    ),
                    ensure_ascii=False,
                )
            )
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
        research_model="qwen-deep-research",
        structurer_model="qwen-plus",
        research_timeout_seconds=45,
        structurer_timeout_seconds=20,
    )

    request = ResearchRequest(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id="run-1",
    )
    result = await provider.research(request)
    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]

    assert result.what_it_is == "AI agent framework"
    assert result.fit_for == "适合平台工程团队"
    assert result.not_for == "不适合完全离线环境"
    assert result.trial_verdict == "needs_complex_setup"
    assert result.trial_requirements[0].label == "Docker"
    assert result.trial_requirements[0].detail == "依赖 docker compose 启动本地服务"
    assert result.trial_requirements[0].source == "README / Setup"
    assert result.trial_time_estimate == "10-20 分钟"
    assert result.quickstart_steps[0].label == "启动服务栈"
    assert result.quickstart_steps[0].action == "运行 `docker compose up`"
    assert result.quickstart_steps[0].expected_result == "核心服务 healthy 且可访问"
    assert result.quickstart_steps[0].source == "README / Quick Start"
    assert result.success_signal == "Web UI 在 localhost 正常响应"
    assert result.common_blockers[0].label == "端口冲突"
    assert result.common_blockers[0].detail == "本地 3000/8080 端口占用会导致启动失败"
    assert result.common_blockers[0].source == "README / Troubleshooting"
    assert result.best_practices == ["先从官方示例开始"]
    assert result.risks == ["依赖外部模型服务"]
    assert len(result.citations) == 1
    assert result.citations[0].title == "README"
    assert result.citations[0].url == "https://github.com/acme/agent"
    assert result.metadata["provider"] == "dashscope"
    assert result.metadata["model"] == "qwen-deep-research"
    assert result.metadata["source_batch"] == "structurer-batch"
    assert len(research_client.calls) == 1
    assert research_client.calls[0]["model"] == "qwen-deep-research"
    assert research_client.calls[0]["api_key"] == "dash-key"
    assert research_client.calls[0]["stream"] is True
    assert research_client.calls[0]["request_timeout"] == 45
    assert research_client.calls[0]["output_format"] == "model_summary_report"
    assert "中文工程团队" in research_client.calls[0]["messages"][0]["content"]
    assert "公开网络资料" in research_client.calls[0]["messages"][0]["content"]

    assert len(structurer_client.calls) == 1
    structurer_call = structurer_client.calls[0]
    assert structurer_call["model"] == "qwen-plus"
    assert structurer_call["result_format"] == "message"
    assert structurer_call["response_format"] == {"type": "json_object"}
    assert structurer_call["request_timeout"] == 20
    assert "JSON" in structurer_call["messages"][0]["content"]
    structurer_prompt = structurer_call["messages"][1]["content"]
    assert "第一段第二段" in structurer_prompt
    assert '"trial_verdict":' in structurer_prompt
    assert '"trial_requirements": [' in structurer_prompt
    assert '"quickstart_steps": [' in structurer_prompt
    assert '"success_signal": "字符串"' in structurer_prompt
    assert '"common_blockers": [' in structurer_prompt
    assert "quickstart 已移除，必须改用 quickstart_steps" in structurer_prompt
    assert "最短且现实可行的首次运行路径" in structurer_prompt
    assert "不能确认命令时，必须明确写“信息不足以确认”" in structurer_prompt
    assert "trial_verdict、quickstart_steps、success_signal 三者必须相互一致" in structurer_prompt
    assert '"quickstart": "字符串"' not in structurer_prompt
    assert any(
        payload["event"] == "research.started"
        and payload["provider"] == "dashscope"
        and payload["model"] == "qwen-deep-research"
        and payload["research_run_id"] == "run-1"
        for payload in payloads
    )
    progress_payloads = [
        payload
        for payload in payloads
        if payload["event"] == "research.progress" and payload["research_run_id"] == "run-1"
    ]
    assert {payload["stage"] for payload in progress_payloads} == {
        "report_turn",
        "structure_report",
    }
    assert all(isinstance(payload["elapsed_ms"], int) for payload in progress_payloads)
    assert any(
        payload["event"] == "research.completed"
        and payload["research_run_id"] == "run-1"
        and payload["citations_count"] == 1
        and payload["best_practices_count"] == 1
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_dashscope_provider_falls_back_to_research_references_when_structured_citations_missing():
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    research_client = _FakeGenerationClient(
        responses=[
            [
                _Chunk(
                    "完整研究报告",
                    references=[
                        {
                            "title": "Official Docs",
                            "url": "https://example.com/docs",
                            "description": "官方文档",
                        }
                    ],
                )
            ],
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            _StructuredResponse(
                json.dumps(
                    _structured_payload(
                        what_it_is="something",
                        why_now="why",
                        fit_for="fit",
                        not_for="not-fit",
                        best_practices=[],
                        risks=[],
                        citations=[],
                    )
                )
            )
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
    )

    result = await provider.research(
        ResearchRequest(
            full_name="acme/agent",
            repo_url="https://github.com/acme/agent",
            research_run_id="run-2",
        )
    )

    assert len(result.citations) == 1
    assert result.citations[0].title == "Official Docs"
    assert result.citations[0].url == "https://example.com/docs"
    assert result.citations[0].snippet == "官方文档"


@pytest.mark.asyncio
async def test_dashscope_provider_raises_value_error_for_invalid_structured_json():
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    research_client = _FakeGenerationClient(
        responses=[
            [_Chunk("完整研究报告")],
        ]
    )
    structurer_client = _FakeGenerationClient(responses=[_StructuredResponse("not-json")])
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
    )

    with pytest.raises(ValueError):
        await provider.research(
            ResearchRequest(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                research_run_id="run-3",
            )
        )


@pytest.mark.asyncio
async def test_dashscope_provider_uses_shared_parser_and_rejects_legacy_quickstart_field():
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    research_client = _FakeGenerationClient(
        responses=[
            [_Chunk("完整研究报告")],
        ]
    )
    payload = _structured_payload()
    payload["quickstart"] = "legacy quickstart paragraph"
    structurer_client = _FakeGenerationClient(
        responses=[
            _StructuredResponse(json.dumps(payload, ensure_ascii=False)),
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
    )

    with pytest.raises(ValueError, match="payload.quickstart is no longer supported"):
        await provider.research(
            ResearchRequest(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                research_run_id="run-legacy-quickstart",
            )
        )


@pytest.mark.asyncio
async def test_dashscope_provider_uses_shared_parser_semantic_validation_for_local_trial_steps():
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    research_client = _FakeGenerationClient(
        responses=[
            [_Chunk("完整研究报告")],
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            _StructuredResponse(
                json.dumps(
                    _structured_payload(
                        trial_verdict="can_run_locally",
                        quickstart_steps=[],
                    ),
                    ensure_ascii=False,
                )
            )
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
    )

    with pytest.raises(ValueError, match="quickstart_steps must not be empty when trial_verdict is can_run_locally"):
        await provider.research(
            ResearchRequest(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                research_run_id="run-semantic-validator",
            )
        )


@pytest.mark.asyncio
async def test_dashscope_provider_requires_concrete_success_signal_for_local_trials():
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    research_client = _FakeGenerationClient(
        responses=[
            [_Chunk("完整研究报告")],
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            _StructuredResponse(
                json.dumps(
                    _structured_payload(
                        trial_verdict="can_run_locally",
                        success_signal="信息不足以确认",
                    ),
                    ensure_ascii=False,
                )
            )
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
    )

    with pytest.raises(ValueError, match="success_signal must be concrete"):
        await provider.research(
            ResearchRequest(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                research_run_id="run-semantic-success-signal",
            )
        )


@pytest.mark.asyncio
async def test_dashscope_provider_wraps_research_transport_errors_with_readable_message(caplog):
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    caplog.set_level(logging.INFO)
    research_client = _FakeGenerationClient(
        responses=[
            requests.exceptions.ConnectionError("Read timed out."),
        ]
    )
    structurer_client = _FakeGenerationClient(responses=[])
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
        research_max_retries=0,
    )

    with pytest.raises(RuntimeError, match="研究报告阶段"):
        await provider.research(
            ResearchRequest(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                research_run_id="run-4",
            )
        )

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert any(
        payload["event"] == "research.failed"
        and payload["research_run_id"] == "run-4"
        and payload["stage"] == "report_turn"
        and payload["exception_type"] == "ConnectionError"
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_dashscope_provider_retries_retryable_report_stream_failures(caplog, monkeypatch):
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    caplog.set_level(logging.INFO)
    sleep_calls = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("repo_pulse.research.dashscope_provider.asyncio.sleep", _fake_sleep)

    research_client = _FakeGenerationClient(
        responses=[
            _RaisingStream(
                [_Chunk("第一段")],
                requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
            ),
            [_Chunk("完整研究报告", references=[{"title": "README", "url": "https://github.com/acme/agent"}])],
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            _StructuredResponse(
                json.dumps(
                    _structured_payload(citations=[]),
                    ensure_ascii=False,
                )
            )
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
        research_max_retries=2,
        research_retry_backoff_seconds=1,
    )

    result = await provider.research(
        ResearchRequest(
            full_name="acme/agent",
            repo_url="https://github.com/acme/agent",
            research_run_id="run-retry-success",
        )
    )

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert result.what_it_is == "AI agent framework"
    assert len(research_client.calls) == 2
    assert sleep_calls == [1]
    assert any(
        payload["event"] == "research.retry"
        and payload["research_run_id"] == "run-retry-success"
        and payload["stage"] == "report_turn"
        and payload["attempt"] == 1
        and payload["max_attempts"] == 3
        and payload["exception_type"] == "ChunkedEncodingError"
        and payload["partial_chars"] == 3
        and payload["chunk_count"] == 1
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_dashscope_provider_reports_partial_progress_when_retry_budget_exhausted(caplog, monkeypatch):
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    caplog.set_level(logging.INFO)
    sleep_calls = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("repo_pulse.research.dashscope_provider.asyncio.sleep", _fake_sleep)

    research_client = _FakeGenerationClient(
        responses=[
            _RaisingStream(
                [_Chunk("第一段"), _Chunk("第二段")],
                requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
            ),
            _RaisingStream(
                [_Chunk("第三段")],
                requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
            ),
            _RaisingStream(
                [_Chunk("第四段")],
                requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
            ),
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=_FakeGenerationClient(responses=[]),
        api_key="dash-key",
        research_max_retries=2,
        research_retry_backoff_seconds=1,
    )

    with pytest.raises(RuntimeError, match="研究报告阶段"):
        await provider.research(
            ResearchRequest(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                research_run_id="run-retry-failed",
            )
        )

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert len(research_client.calls) == 3
    assert sleep_calls == [1, 2]
    assert any(
        payload["event"] == "research.failed"
        and payload["research_run_id"] == "run-retry-failed"
        and payload["stage"] == "report_turn"
        and payload["attempt"] == 3
        and payload["max_attempts"] == 3
        and payload["exception_type"] == "ChunkedEncodingError"
        and payload["partial_chars"] == 3
        and payload["chunk_count"] == 1
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_dashscope_provider_retries_retryable_structurer_failures(caplog, monkeypatch):
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    caplog.set_level(logging.INFO)
    sleep_calls = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("repo_pulse.research.dashscope_provider.asyncio.sleep", _fake_sleep)

    research_client = _FakeGenerationClient(
        responses=[
            [_Chunk("完整研究报告", references=[{"title": "README", "url": "https://github.com/acme/agent"}])],
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            requests.exceptions.ConnectionError("Connection reset by peer"),
            _StructuredResponse(
                json.dumps(
                    _structured_payload(citations=[]),
                    ensure_ascii=False,
                )
            ),
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
        structurer_max_retries=2,
        structurer_retry_backoff_seconds=1,
    )

    result = await provider.research(
        ResearchRequest(
            full_name="acme/agent",
            repo_url="https://github.com/acme/agent",
            research_run_id="run-structurer-retry-success",
        )
    )

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert result.what_it_is == "AI agent framework"
    assert len(structurer_client.calls) == 2
    assert sleep_calls == [1]
    assert any(
        payload["event"] == "research.retry"
        and payload["research_run_id"] == "run-structurer-retry-success"
        and payload["stage"] == "structure_report"
        and payload["attempt"] == 1
        and payload["max_attempts"] == 3
        and payload["exception_type"] == "ConnectionError"
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_dashscope_provider_reports_structurer_retry_exhaustion(caplog, monkeypatch):
    from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider

    caplog.set_level(logging.INFO)
    sleep_calls = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("repo_pulse.research.dashscope_provider.asyncio.sleep", _fake_sleep)

    research_client = _FakeGenerationClient(
        responses=[
            [_Chunk("完整研究报告")],
        ]
    )
    structurer_client = _FakeGenerationClient(
        responses=[
            requests.exceptions.Timeout("Request timed out"),
            requests.exceptions.Timeout("Request timed out"),
        ]
    )
    provider = DashScopeDeepResearchProvider(
        research_client=research_client,
        structurer_client=structurer_client,
        api_key="dash-key",
        structurer_max_retries=1,
        structurer_retry_backoff_seconds=1,
    )

    with pytest.raises(RuntimeError, match="结构化阶段"):
        await provider.research(
            ResearchRequest(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                research_run_id="run-structurer-retry-failed",
            )
        )

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert len(structurer_client.calls) == 2
    assert sleep_calls == [1]
    assert any(
        payload["event"] == "research.failed"
        and payload["research_run_id"] == "run-structurer-retry-failed"
        and payload["stage"] == "structure_report"
        and payload["attempt"] == 2
        and payload["max_attempts"] == 2
        and payload["exception_type"] == "Timeout"
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )
