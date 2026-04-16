from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from repo_pulse.research.evidence import RepositoryEvidence


@dataclass
class Citation:
    title: str
    url: str
    snippet: Optional[str] = None


TRIAL_VERDICT_CAN_RUN_LOCALLY = "can_run_locally"
TRIAL_VERDICT_NEEDS_API_KEY = "needs_api_key"
TRIAL_VERDICT_NEEDS_CLOUD_RESOURCE = "needs_cloud_resource"
TRIAL_VERDICT_NEEDS_COMPLEX_SETUP = "needs_complex_setup"
TRIAL_VERDICT_SOURCE_READING_ONLY = "source_reading_only"
TRIAL_VERDICT_INSUFFICIENT_INFORMATION = "insufficient_information"

TRIAL_VERDICTS = {
    TRIAL_VERDICT_CAN_RUN_LOCALLY,
    TRIAL_VERDICT_NEEDS_API_KEY,
    TRIAL_VERDICT_NEEDS_CLOUD_RESOURCE,
    TRIAL_VERDICT_NEEDS_COMPLEX_SETUP,
    TRIAL_VERDICT_SOURCE_READING_ONLY,
    TRIAL_VERDICT_INSUFFICIENT_INFORMATION,
}


@dataclass
class OnboardingFact:
    label: str
    detail: str
    source: str


@dataclass
class QuickstartStep:
    label: str
    action: str
    expected_result: str
    source: str


@dataclass
class ResearchRequest:
    full_name: str
    repo_url: str
    research_run_id: str
    evidence: Optional[RepositoryEvidence] = None


@dataclass
class ResearchResult:
    what_it_is: str
    why_now: str
    fit_for: str = ""
    not_for: str = ""
    trial_verdict: str = TRIAL_VERDICT_INSUFFICIENT_INFORMATION
    trial_requirements: List[OnboardingFact] = field(default_factory=list)
    trial_time_estimate: str = ""
    quickstart_steps: List[QuickstartStep] = field(default_factory=list)
    success_signal: str = ""
    common_blockers: List[OnboardingFact] = field(default_factory=list)
    best_practices: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


def parse_research_result_payload(
    payload, *, citations: List[Citation], metadata: dict[str, str]
) -> ResearchResult:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "quickstart" in payload:
        raise ValueError("payload.quickstart is no longer supported; use quickstart_steps instead")

    payload_citations = _parse_citations(citations)
    provider_metadata = _parse_metadata(metadata, field_name="metadata")
    payload_metadata = _parse_metadata(payload.get("metadata", {}), field_name="payload.metadata")

    trial_verdict = _parse_trial_verdict(payload.get("trial_verdict", TRIAL_VERDICT_INSUFFICIENT_INFORMATION))
    quickstart_steps = _parse_quickstart_steps(payload.get("quickstart_steps", []), field_name="quickstart_steps")
    success_signal = _parse_text_field(payload, "success_signal")
    if trial_verdict == TRIAL_VERDICT_CAN_RUN_LOCALLY and not quickstart_steps:
        raise ValueError("quickstart_steps must not be empty when trial_verdict is can_run_locally")
    if trial_verdict == TRIAL_VERDICT_CAN_RUN_LOCALLY and _is_placeholder_text(success_signal):
        raise ValueError("success_signal must be concrete when trial_verdict is can_run_locally")
    if trial_verdict == TRIAL_VERDICT_SOURCE_READING_ONLY and quickstart_steps:
        raise ValueError("quickstart_steps must be empty when trial_verdict is source_reading_only")

    return ResearchResult(
        what_it_is=_parse_text_field(payload, "what_it_is", required=True),
        why_now=_parse_text_field(payload, "why_now", required=True),
        fit_for=_parse_text_field(payload, "fit_for"),
        not_for=_parse_text_field(payload, "not_for"),
        trial_verdict=trial_verdict,
        trial_requirements=_parse_onboarding_facts(
            payload.get("trial_requirements", []), field_name="trial_requirements"
        ),
        trial_time_estimate=_parse_text_field(payload, "trial_time_estimate"),
        quickstart_steps=quickstart_steps,
        success_signal=success_signal,
        common_blockers=_parse_onboarding_facts(payload.get("common_blockers", []), field_name="common_blockers"),
        best_practices=_parse_string_list(payload.get("best_practices", []), field_name="best_practices"),
        risks=_parse_string_list(payload.get("risks", []), field_name="risks"),
        citations=payload_citations,
        metadata={**payload_metadata, **provider_metadata},
    )


def _parse_text_field(payload: dict, field_name: str, *, required: bool = False) -> str:
    value = payload.get(field_name, "")
    if value == "" and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _parse_string_list(value, *, field_name: str) -> List[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of non-empty strings")

    items: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} must be a list of non-empty strings")
        items.append(item)
    return items


def _parse_onboarding_facts(value, *, field_name: str) -> List[OnboardingFact]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of objects with label, detail, and source")

    items: List[OnboardingFact] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be a list of objects with label, detail, and source")

        label = item.get("label", "")
        detail = item.get("detail", "")
        source = item.get("source", "")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"{field_name}.label must be a non-empty string")
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError(f"{field_name}.detail must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"{field_name}.source must be a non-empty string")
        items.append(OnboardingFact(label=label, detail=detail, source=source))

    return items


def _parse_quickstart_steps(value, *, field_name: str) -> List[QuickstartStep]:
    if not isinstance(value, list):
        raise ValueError(
            f"{field_name} must be a list of objects with label, action, expected_result, and source"
        )

    items: List[QuickstartStep] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(
                f"{field_name} must be a list of objects with label, action, expected_result, and source"
            )

        label = item.get("label", "")
        action = item.get("action", "")
        expected_result = item.get("expected_result", "")
        source = item.get("source", "")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"{field_name}.label must be a non-empty string")
        if not isinstance(action, str) or not action.strip():
            raise ValueError(f"{field_name}.action must be a non-empty string")
        if not isinstance(expected_result, str) or not expected_result.strip():
            raise ValueError(f"{field_name}.expected_result must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"{field_name}.source must be a non-empty string")
        items.append(
            QuickstartStep(
                label=label,
                action=action,
                expected_result=expected_result,
                source=source,
            )
        )

    return items


def _parse_trial_verdict(value) -> str:
    if not isinstance(value, str) or value not in TRIAL_VERDICTS:
        raise ValueError("trial_verdict must be one of the supported trial verdict constants")
    return value


def _parse_citations(citations) -> List[Citation]:
    if not isinstance(citations, list) or any(not isinstance(citation, Citation) for citation in citations):
        raise ValueError("citations must be a list of Citation objects")
    return list(citations)


def _parse_metadata(value, *, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")

    parsed: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{field_name} must only contain string keys and values")
        parsed[key] = item
    return parsed


def _is_placeholder_text(value: str) -> bool:
    return value.strip() == "信息不足以确认"


class ResearchProvider(ABC):
    @abstractmethod
    async def research(self, request: ResearchRequest) -> ResearchResult:
        raise NotImplementedError
