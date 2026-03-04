"""
Instrumentation tracker for schema indexing and other long-running operations.
Provides structured logging for start, underway, passed, and failed states.
"""

import logging

logger = logging.getLogger("daibai.instrumentation")
_tracker_context: list = []


def init_tracker(operation: str) -> None:
    """Initialize tracker context for an operation."""
    _tracker_context.append(operation)
    logger.info("[instrumentation] init: %s", operation)


def _clear_tracker() -> None:
    if _tracker_context:
        _tracker_context.pop()


def track_start(operation: str, detail: str = "") -> None:
    """Log operation start."""
    msg = f"[instrumentation] START: {operation}"
    if detail:
        msg += f" | {detail}"
    logger.info(msg)


def track_underway(operation: str, detail: str) -> None:
    """Log operation in progress."""
    logger.info("[instrumentation] UNDERWAY: %s | %s", operation, detail)


def track_passed(operation: str, detail: str) -> None:
    """Log operation success."""
    logger.info("[instrumentation] PASSED: %s | %s", operation, detail)
    _clear_tracker()


def track_failed(operation: str, detail: str) -> None:
    """Log operation failure."""
    logger.warning("[instrumentation] FAILED: %s | %s", operation, detail)
    _clear_tracker()
