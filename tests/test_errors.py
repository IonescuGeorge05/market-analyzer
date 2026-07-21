"""
Tests for errors.reason() - extracting the provider's real message.

Why these exist: a stale-model 400 and an out-of-credits 400 are both
"BadRequestError", and telling them apart cost real debugging time once.
The first version of this function ran past the message and swallowed
request_id, so the boundary gets a test.
"""
from app.errors import reason

CREDIT_400 = (
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'Your credit balance is too low to "
    "access the Anthropic API. Please go to Plans & Billing to upgrade or "
    "purchase credits.'}, 'request_id': 'req_011CdFGyM5HQ4Xf4vDUHzSLL'}"
)

MODEL_400 = (
    'Error code: 400 - {"type": "error", "error": {"type": '
    '"invalid_request_error", "message": "model: claude-nonexistent-1"}, '
    '"request_id": "req_abc123"}'
)


def test_extracts_single_quoted_message():
    assert reason(Exception(CREDIT_400)) == (
        "Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits."
    )


def test_extracts_double_quoted_message():
    assert reason(Exception(MODEL_400)) == "model: claude-nonexistent-1"


def test_does_not_leak_trailing_fields():
    """Regression: rsplit used to run past the message into request_id."""
    out = reason(Exception(CREDIT_400))
    assert "request_id" not in out
    assert "req_011" not in out
    assert not out.endswith("'")


def test_two_different_400s_are_distinguishable():
    """The whole point: same exception class, different actionable cause."""
    assert reason(Exception(CREDIT_400)) != reason(Exception(MODEL_400))


def test_plain_exception_passes_through():
    assert reason(ValueError("connection reset")) == "connection reset"


def test_empty_exception_does_not_crash():
    assert reason(Exception()) == ""


def test_output_is_truncated():
    assert len(reason(Exception("x" * 5000))) == 200
