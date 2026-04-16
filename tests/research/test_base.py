import pytest

from repo_pulse.research.base import (
    TRIAL_VERDICT_CAN_RUN_LOCALLY,
    TRIAL_VERDICT_NEEDS_API_KEY,
    TRIAL_VERDICT_SOURCE_READING_ONLY,
    Citation,
    OnboardingFact,
    QuickstartStep,
    ResearchResult,
    parse_research_result_payload,
)


def _payload(**overrides):
    payload = {
        "what_it_is": "An agent framework for internal tooling",
        "why_now": "The ecosystem is mature enough for narrow trials",
        "fit_for": "Platform teams evaluating agent workflows",
        "not_for": "Strictly air-gapped production deployments",
        "trial_verdict": TRIAL_VERDICT_NEEDS_API_KEY,
        "trial_requirements": [
            {
                "label": "API key",
                "detail": "Requires an OpenAI-compatible API key for the first run",
                "source": "docs/getting-started.md",
            }
        ],
        "trial_time_estimate": "10-15 minutes",
        "quickstart_steps": [
            {
                "label": "Install dependencies",
                "action": "Create a virtualenv and install the package with uv",
                "expected_result": "The package installs without dependency resolution errors",
                "source": "README.md",
            },
            {
                "label": "Run the example",
                "action": "Execute the sample script with the configured API key",
                "expected_result": "The sample script prints a successful agent response",
                "source": "examples/basic.py",
            },
        ],
        "success_signal": "The example script returns a successful agent response",
        "common_blockers": [
            {
                "label": "Missing API credentials",
                "detail": "The sample fails if the provider API key is absent from the environment",
                "source": "README.md",
            },
            {
                "label": "Unsupported Python version",
                "detail": "The package requires Python 3.11 or newer",
                "source": "pyproject.toml",
            },
        ],
        "best_practices": ["Start from the official example"],
        "risks": ["Hosted model dependency"],
        "metadata": {
            "provider": "payload-provider",
            "source_kind": "readme",
        },
    }
    payload.update(overrides)
    return payload


def test_parse_research_result_payload_builds_contract_and_merges_metadata():
    citations = [
        Citation(
            title="README",
            url="https://github.com/acme/agent",
            snippet="Quickstart example",
        )
    ]

    result = parse_research_result_payload(
        _payload(),
        citations=citations,
        metadata={"provider": "openai", "model": "gpt-5"},
    )

    assert result == ResearchResult(
        what_it_is="An agent framework for internal tooling",
        why_now="The ecosystem is mature enough for narrow trials",
        fit_for="Platform teams evaluating agent workflows",
        not_for="Strictly air-gapped production deployments",
        trial_verdict=TRIAL_VERDICT_NEEDS_API_KEY,
        trial_requirements=[
            OnboardingFact(
                label="API key",
                detail="Requires an OpenAI-compatible API key for the first run",
                source="docs/getting-started.md",
            )
        ],
        trial_time_estimate="10-15 minutes",
        quickstart_steps=[
            QuickstartStep(
                label="Install dependencies",
                action="Create a virtualenv and install the package with uv",
                expected_result="The package installs without dependency resolution errors",
                source="README.md",
            ),
            QuickstartStep(
                label="Run the example",
                action="Execute the sample script with the configured API key",
                expected_result="The sample script prints a successful agent response",
                source="examples/basic.py",
            ),
        ],
        success_signal="The example script returns a successful agent response",
        common_blockers=[
            OnboardingFact(
                label="Missing API credentials",
                detail="The sample fails if the provider API key is absent from the environment",
                source="README.md",
            ),
            OnboardingFact(
                label="Unsupported Python version",
                detail="The package requires Python 3.11 or newer",
                source="pyproject.toml",
            ),
        ],
        best_practices=["Start from the official example"],
        risks=["Hosted model dependency"],
        citations=citations,
        metadata={
            "provider": "openai",
            "model": "gpt-5",
            "source_kind": "readme",
        },
    )


def test_parse_research_result_payload_rejects_legacy_quickstart_field():
    with pytest.raises(ValueError, match="quickstart"):
        parse_research_result_payload(
            _payload(quickstart="pip install acme-agent"),
            citations=[],
            metadata={},
        )


def test_parse_research_result_payload_rejects_unknown_trial_verdict():
    with pytest.raises(ValueError, match="trial_verdict"):
        parse_research_result_payload(
            _payload(trial_verdict="works_on_my_machine"),
            citations=[],
            metadata={},
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("trial_requirements", ["Needs an API key"]),
        ("quickstart_steps", ["Install dependencies"]),
        ("common_blockers", ["Missing API credentials"]),
    ],
)
def test_parse_research_result_payload_rejects_unstructured_onboarding_lists(field_name, field_value):
    with pytest.raises(ValueError, match=field_name):
        parse_research_result_payload(
            _payload(**{field_name: field_value}),
            citations=[],
            metadata={},
        )


@pytest.mark.parametrize(
    ("field_name", "field_value", "error_match"),
    [
        (
            "trial_requirements",
            [{"title": "API key", "detail": "Need a key"}],
            r"trial_requirements\.(label|source)",
        ),
        (
            "quickstart_steps",
            [{"title": "Install", "detail": "Run uv sync"}],
            r"quickstart_steps\.(label|action|expected_result|source)",
        ),
        (
            "common_blockers",
            [{"title": "Missing API key", "detail": "Set env var"}],
            r"common_blockers\.(label|source)",
        ),
    ],
)
def test_parse_research_result_payload_rejects_legacy_structured_shapes(field_name, field_value, error_match):
    with pytest.raises(ValueError, match=error_match):
        parse_research_result_payload(
            _payload(**{field_name: field_value}),
            citations=[],
            metadata={},
        )


def test_parse_research_result_payload_requires_steps_for_local_trials():
    with pytest.raises(ValueError, match="quickstart_steps"):
        parse_research_result_payload(
            _payload(
                trial_verdict=TRIAL_VERDICT_CAN_RUN_LOCALLY,
                quickstart_steps=[],
            ),
            citations=[],
            metadata={},
        )


def test_parse_research_result_payload_requires_concrete_success_signal_for_local_trials():
    with pytest.raises(ValueError, match="success_signal"):
        parse_research_result_payload(
            _payload(
                trial_verdict=TRIAL_VERDICT_CAN_RUN_LOCALLY,
                success_signal="信息不足以确认",
            ),
            citations=[],
            metadata={},
        )


def test_parse_research_result_payload_rejects_reading_only_verdict_with_runnable_steps():
    with pytest.raises(ValueError, match="source_reading_only"):
        parse_research_result_payload(
            _payload(
                trial_verdict=TRIAL_VERDICT_SOURCE_READING_ONLY,
            ),
            citations=[],
            metadata={},
        )


def test_research_result_no_longer_accepts_legacy_quickstart_field():
    with pytest.raises(TypeError, match="quickstart"):
        ResearchResult(
            what_it_is="An agent framework for internal tooling",
            why_now="The ecosystem is mature enough for narrow trials",
            trial_verdict=TRIAL_VERDICT_NEEDS_API_KEY,
            quickstart="legacy string",
        )
