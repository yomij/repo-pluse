import json
import logging
import sys
from typing import Any


_HANDLER_MARKER = "_repo_pulse_observability_handler"
_RESERVED_ENVELOPE_KEYS = {"timestamp", "level", "logger", "message", "exception"}
_LIBRARY_LOGGERS_TO_NORMALIZE = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "apscheduler",
    "apscheduler.scheduler",
    "Lark",
)


def _json_default(value: Any) -> str:
    return repr(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {}

        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(
                {
                    key: value
                    for key, value in event_data.items()
                    if key not in _RESERVED_ENVELOPE_KEYS
                }
            )

        payload.update(
            {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=_json_default)


def configure_logging(level: int = logging.INFO) -> None:
    root_logger = logging.getLogger()

    for handler in root_logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    root_logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    root_logger.addHandler(handler)

    for logger_name in _LIBRARY_LOGGERS_TO_NORMALIZE:
        logger = logging.getLogger(logger_name)
        for library_handler in logger.handlers[:]:
            logger.removeHandler(library_handler)
        logger.propagate = True


def log_research_event(
    logger: logging.Logger,
    *,
    event: str,
    status: str,
    research_run_id: str,
    repo_full_name: str,
    message: str,
    **event_fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "status": status,
        "research_run_id": research_run_id,
        "repo_full_name": repo_full_name,
    }
    payload.update(event_fields)
    logger.info(message, extra={"event_data": payload})
