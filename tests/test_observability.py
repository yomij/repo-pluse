import json
import logging
import sys


def test_json_formatter_serializes_event_data():
    from repo_pulse.observability import JsonFormatter

    record = logging.LogRecord(
        name="repo_pulse.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="detail request accepted",
        args=(),
        exc_info=None,
    )
    record.event_data = {
        "event": "detail.request.received",
        "status": "started",
        "research_run_id": "run-1",
        "repo_full_name": "acme/agent",
    }

    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["event"] == "detail.request.received"
    assert payload["research_run_id"] == "run-1"
    assert payload["message"] == "detail request accepted"


def test_log_research_event_attaches_structured_payload(caplog):
    from repo_pulse.observability import log_research_event

    logger = logging.getLogger("repo_pulse.test.observability")
    caplog.set_level(logging.INFO, logger="repo_pulse.test.observability")

    log_research_event(
        logger,
        event="research.started",
        status="started",
        research_run_id="run-2",
        repo_full_name="acme/agent",
        provider="dashscope",
        model="qwen-deep-research",
        message="research provider started",
    )

    record = caplog.records[-1]
    assert record.event_data["event"] == "research.started"
    assert record.event_data["provider"] == "dashscope"
    assert record.event_data["model"] == "qwen-deep-research"


def test_json_formatter_serializes_non_json_event_values():
    from repo_pulse.observability import JsonFormatter

    class NonSerializableValue:
        def __repr__(self) -> str:
            return "<NonSerializableValue>"

    record = logging.LogRecord(
        name="repo_pulse.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=57,
        msg="research context accepted",
        args=(),
        exc_info=None,
    )
    record.event_data = {
        "event": "detail.request.received",
        "status": "started",
        "unsupported": NonSerializableValue(),
    }

    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["unsupported"] == "<NonSerializableValue>"


def test_json_formatter_reserves_envelope_keys():
    from repo_pulse.observability import JsonFormatter

    try:
        raise RuntimeError("actual failure")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="repo_pulse.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=85,
        msg="real message",
        args=(),
        exc_info=exc_info,
    )
    record.event_data = {
        "event": "detail.request.received",
        "timestamp": "shadowed-timestamp",
        "level": "shadowed-level",
        "logger": "shadowed-logger",
        "message": "shadowed-message",
        "exception": "shadowed-exception",
    }

    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["event"] == "detail.request.received"
    assert payload["timestamp"] != "shadowed-timestamp"
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "repo_pulse.test"
    assert payload["message"] == "real message"
    assert payload["exception"] != "shadowed-exception"
    assert "actual failure" in payload["exception"]


def test_configure_logging_replaces_existing_handlers_and_normalizes_library_loggers():
    from repo_pulse.observability import JsonFormatter, configure_logging

    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    library_loggers = {
        name: logging.getLogger(name)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "apscheduler.scheduler", "Lark")
    }
    original_library_state = {
        name: (logger.handlers[:], logger.propagate)
        for name, logger in library_loggers.items()
    }

    try:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        foreign_handler = logging.NullHandler()
        root_logger.addHandler(foreign_handler)
        root_logger.setLevel(logging.WARNING)
        for logger in library_loggers.values():
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False

        configure_logging(level=logging.ERROR)
        handlers_after_first_call = root_logger.handlers[:]
        level_after_first_call = root_logger.level

        assert len(handlers_after_first_call) == 1
        assert isinstance(handlers_after_first_call[0].formatter, JsonFormatter)
        assert root_logger.level == logging.ERROR
        for logger in library_loggers.values():
            assert logger.handlers == []
            assert logger.propagate is True

        configure_logging(level=logging.DEBUG)

        assert root_logger.handlers == handlers_after_first_call
        assert root_logger.level == level_after_first_call
    finally:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        for handler in original_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(original_level)
        for name, logger in library_loggers.items():
            logger.handlers.clear()
            for handler in original_library_state[name][0]:
                logger.addHandler(handler)
            logger.propagate = original_library_state[name][1]
