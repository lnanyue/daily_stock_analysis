# -*- coding: utf-8 -*-
"""
Exception hierarchy for error classification across the notification layer.

Callers should classify I/O errors by raising:
  - **RetryableError**  : temporary failures (timeout, connect error, 429, DNS blip)
  - **NonRetryableError**: permanent failures (auth failure, 4xx except 429, invalid config)

Any **other** exception type reaching the retry boundary is treated as retryable and
logged with full traceback so that new failure modes are not silently swallowed.
"""

import inspect

logger = None  # lazy-init to avoid import-time circular dependency

def _get_logger():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger(__name__)
    return logger


class RetryableError(Exception):
    """Temporary failure that may succeed on retry (timeout, rate-limit, network blip)."""


class NonRetryableError(Exception):
    """Permanent failure that will not be fixed by retrying
    (auth failure, invalid config, unsupported HTTP method, etc.)."""
