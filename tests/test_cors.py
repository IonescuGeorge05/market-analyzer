"""
Tests for the CORS origin rule.

Why this exists: Vite silently moves to 5174 when 5173 is taken. With a
hardcoded allowlist the browser then discarded a perfectly good 200 response,
so the server logged success while the UI showed "Failed to fetch". Failing
in two different places at once is a bad way to spend an afternoon.
"""
import re

import pytest

from app.main import app


def _origin_regex():
    """The compiled regex CORSMiddleware was configured with."""
    for m in app.user_middleware:
        opts = getattr(m, "kwargs", {}) or {}
        if "allow_origin_regex" in opts:
            return re.compile(opts["allow_origin_regex"])
    pytest.fail("no allow_origin_regex configured on the app")


@pytest.mark.parametrize("origin", [
    "http://localhost:5173",     # vite default
    "http://localhost:5174",     # vite when 5173 is taken - the actual bug
    "http://localhost:5175",
    "http://localhost:3000",     # create-react-app default
    "http://127.0.0.1:5173",     # some browsers resolve it this way
    "http://127.0.0.1:8000",
])
def test_local_dev_origins_are_allowed(origin):
    assert _origin_regex().fullmatch(origin), f"{origin} would be blocked"


@pytest.mark.parametrize("origin", [
    "http://evil.com",
    "https://localhost.evil.com",
    "http://notlocalhost:5173",
])
def test_non_local_origins_are_not_allowed(origin):
    assert not _origin_regex().fullmatch(origin), f"{origin} should be blocked"
