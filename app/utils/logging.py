"""
Logging configuration using structlog.
"""

import logging
import sys
from typing import Optional

import structlog

from app.config import settings


def setup_logging(log_level: Optional[str] = None) -> None:
    """
    Configure structured logging.

    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    log_level = log_level or ("DEBUG" if settings.debug else "INFO")

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            # Use colored output in development, JSON in production
            structlog.dev.ConsoleRenderer()
            if settings.debug
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level),
    )

    # Suppress noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)


def setup_sentry() -> None:
    """
    Configure Sentry for error tracking.
    """
    if not settings.sentry_dsn:
        return

    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1,  # 10% of transactions
            profiles_sample_rate=0.1,
        )

        structlog.get_logger().info("Sentry initialized")

    except ImportError:
        structlog.get_logger().warning(
            "Sentry SDK not installed, error tracking disabled"
        )
