import pytest

from repo_pulse.digest.localization import DashScopeSummaryLocalizer, PassthroughSummaryLocalizer


class _FakeGenerationClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def call(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_passthrough_summary_localizer_returns_original_text():
    localizer = PassthroughSummaryLocalizer()

    assert await localizer.localize("Agent framework") == "Agent framework"


@pytest.mark.asyncio
async def test_dashscope_summary_localizer_translates_english_summary():
    client = _FakeGenerationClient(
        response={
            "output": {
                "choices": [
                    {
                        "message": {
                            "content": [{"text": "面向任务自动化的 Agent 框架"}],
                        }
                    }
                ]
            }
        }
    )
    localizer = DashScopeSummaryLocalizer(
        generation_client=client,
        api_key="dash-key",
        model="qwen-plus",
    )

    localized = await localizer.localize("Agent framework")

    assert localized == "面向任务自动化的 Agent 框架"
    assert client.calls[0]["model"] == "qwen-plus"
    assert client.calls[0]["api_key"] == "dash-key"


@pytest.mark.asyncio
async def test_dashscope_summary_localizer_skips_cjk_and_falls_back_on_error():
    client = _FakeGenerationClient(error=RuntimeError("boom"))
    localizer = DashScopeSummaryLocalizer(
        generation_client=client,
        api_key="dash-key",
        model="qwen-plus",
    )

    assert await localizer.localize("已经是中文") == "已经是中文"
    assert await localizer.localize("Agent framework") == "Agent framework"
    assert len(client.calls) == 1
