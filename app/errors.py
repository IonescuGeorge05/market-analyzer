"""
Shared error helpers.

Lives outside app/agents/ on purpose: both the Analyzer and the Reporter need
this, and CLAUDE.md rule 1 forbids one agent importing another's internals.
"""


def reason(e: Exception) -> str:
    """Pull the provider's human-readable message out of an API error.

    Anthropic returns 400 for things that are not malformed requests at all
    (an empty credit balance, for one), so the exception class name on its own
    is actively misleading. Surfacing the real text turns a log-diving session
    into a glance at the `warnings` array.
    """
    msg = str(e)
    for sep in ('"message": "', "'message': '"):
        if sep in msg:
            # Cut at the FIRST closing quote. rsplit would run past the end of
            # the message and swallow trailing fields like request_id.
            msg = msg.split(sep, 1)[1].split(sep[-1], 1)[0]
            break
    return msg[:200]
