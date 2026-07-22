"""
Tests for _extract_json_array().

Why this exists: the analyzer intermittently died with
"Expecting value: line 1 column 202 (char 201)" and silently dropped a whole
run to VADER. The prompt asks for bare JSON and usually gets it, but a
response occasionally arrives fenced or with a sentence of preamble, and a
bare json.loads then throws away eight good scores.

Intermittent failures are worse than deterministic ones - this one only
showed up as a wrong number on a dashboard - so the parser is deliberately
forgiving about what surrounds the array.
"""
import pytest

from app.agents.analyzer import _extract_json_array

CLEAN = '[{"i": 1, "score": 0.6, "reasoning": "earnings beat"}]'


def test_bare_array_parses():
    assert _extract_json_array(CLEAN)[0]["score"] == 0.6


def test_leading_and_trailing_whitespace():
    assert len(_extract_json_array(f"\n\n  {CLEAN}  \n")) == 1


def test_json_fenced_block():
    assert len(_extract_json_array(f"```json\n{CLEAN}\n```")) == 1


def test_unlabelled_fenced_block():
    assert len(_extract_json_array(f"```\n{CLEAN}\n```")) == 1


def test_uppercase_fence_label():
    assert len(_extract_json_array(f"```JSON\n{CLEAN}\n```")) == 1


def test_prose_preamble():
    """The most likely real cause: the model explains itself first."""
    raw = f"Sure, here are the sentiment scores you asked for:\n{CLEAN}"
    assert len(_extract_json_array(raw)) == 1


def test_prose_after_the_array():
    raw = f"{CLEAN}\n\nLet me know if you need anything else."
    assert len(_extract_json_array(raw)) == 1


def test_prose_on_both_sides():
    raw = f"Here you go:\n{CLEAN}\nHope that helps."
    assert len(_extract_json_array(raw)) == 1


def test_multi_item_array_keeps_order():
    raw = ('[{"i": 1, "score": -0.4, "reasoning": "a"}, '
           '{"i": 2, "score": 0.8, "reasoning": "b"}]')
    out = _extract_json_array(raw)
    assert [o["i"] for o in out] == [1, 2]


# ---------- the leading-plus bug (regression) ----------

# Captured verbatim from a real gpt-4o-mini response that crashed the batch
# with "Expecting value: line 1 column 202 (char 201)". Note "score": +1.0 -
# JSON permits a leading minus and nothing else.
REAL_PLUS_RESPONSE = (
    '[{"i": 1, "score": -1.0, "reasoning": "lawsuit could negatively impact stock performance"}, '
    '{"i": 2, "score": 0.0, "reasoning": "story is not directly related to stock performance"}, '
    '{"i": 3, "score": +1.0, "reasoning": "entry into foldable market could boost sales"}, '
    '{"i": 4, "score": 0.0, "reasoning": "comparison with Nvidia does not indicate direction"}, '
    '{"i": 5, "score": +0.6, "reasoning": "endorsement suggests confidence"}]'
)


def test_leading_plus_on_scores_is_tolerated():
    out = _extract_json_array(REAL_PLUS_RESPONSE)
    assert [o["score"] for o in out] == [-1.0, 0.0, 1.0, 0.0, 0.6]


def test_negative_scores_keep_their_sign():
    out = _extract_json_array('[{"i": 1, "score": -0.8, "reasoning": "x"}]')
    assert out[0]["score"] == -0.8


def test_plus_inside_reasoning_text_is_untouched():
    """Only numbers are normalized; prose must survive intact."""
    raw = '[{"i": 1, "score": 0.5, "reasoning": "revenue +12% year on year"}]'
    assert _extract_json_array(raw)[0]["reasoning"] == "revenue +12% year on year"


def test_plus_at_the_start_of_an_array_element():
    assert _extract_json_array('[+0.5, -0.5]') == [0.5, -0.5]


def test_no_array_raises_with_a_readable_message():
    with pytest.raises(ValueError, match="no JSON array"):
        _extract_json_array("I cannot score these items.")


def test_empty_response_raises():
    with pytest.raises(ValueError, match="no JSON array"):
        _extract_json_array("")


def test_truncated_array_still_raises():
    """Slicing must not paper over genuinely broken JSON."""
    with pytest.raises(Exception):
        _extract_json_array('[{"i": 1, "score": 0.6, "reason')
