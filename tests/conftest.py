"""pytest configuration."""

import json
import re

import pytest


def _scrub_request_body(request):
    """Scrub sensitive tokens from request body and URI."""
    # Filter token from URI query parameters (e.g. finnhub)
    request.uri = re.sub(r"token=[^&]+", "token=FILTERED", request.uri)

    # Filter token from JSON request body (e.g. tushare)
    if request.body:
        try:
            body = json.loads(request.body)
            if isinstance(body, dict) and "token" in body:
                body["token"] = "FILTERED"
            request.body = json.dumps(body).encode()
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Filter api_key from URI
    request.uri = re.sub(r"api_key=[^&]+", "api_key=FILTERED", request.uri)
    request.uri = re.sub(r"apikey=[^&]+", "apikey=FILTERED", request.uri)

    return request


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "filter_headers": [
            "authorization",
            "Authorization",
            "cookie",
            "Cookie",
            "x-api-key",
            "X-API-Key",
        ],
        "filter_query_parameters": [
            "token",
            "api_key",
            "apikey",
        ],
        "before_record_request": _scrub_request_body,
        "record_mode": "once",
    }
