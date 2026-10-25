"""
JSON structured logging so Filebeat can parse fields directly without
needing a grok pattern. Each log line is one JSON object with:
time, service, severity, message, request_id, and any extra fields.

We never log secrets or personal data (no card numbers, no names/emails).
"""
import logging
import json
import sys
import time
import uuid
from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

SERVICE_NAME = "movie-booking-api"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "service": SERVICE_NAME,
            "severity": record.levelname,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_ctx.get()),
        }
        # allow extra structured fields via logger.info(msg, extra={"extra_fields": {...}})
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)
        return json.dumps(payload)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    return root


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


logger = logging.getLogger("movie_booking")


def log(severity: str, message: str, **extra_fields):
    """Convenience helper: log.log('INFO', 'booking confirmed', booking_id=5, showtime_id=2)"""
    level = getattr(logging, severity.upper(), logging.INFO)
    logger.log(level, message, extra={"extra_fields": extra_fields})
