# -*- coding: utf-8 -*-
"""
Exception hierarchy for error classification.

All retryable / non-retryable errors inherit from a common base so callers
can catch them uniformly without swallowing unexpected exception types.
"""


class RetryableError(Exception):
    """Temporary failure that may succeed on retry (timeout, rate-limit, network blip)."""


class NonRetryableError(Exception):
    """Permanent failure that will not be fixed by retrying (auth failure, invalid config, server error 5xx)."""
