"""
LLM factory.

One place that decides which provider the agents talk to. Lives outside
app/agents/ because both the Analyzer and the Reporter need it, and design
rule 1 forbids one agent importing another agent's internals.

Switching providers is a .env change, not a code change:

    LLM_PROVIDER=openai      # or: anthropic

This is the payoff for going through LangChain rather than calling a provider
SDK directly. Both agents ask for a chat model and neither knows or cares
which company answers.

What does NOT change when you switch: the BUY/HOLD/SELL signal. That is
computed in code in reporter._signal(), so the provider affects the sentiment
scores and the prose, never the decision rule.
"""
import logging

from app.config import settings

log = logging.getLogger(__name__)


def chat(temperature: float, max_tokens: int):
    """Return a configured chat model for the provider named in settings.

    Imports are deliberately inside the branches so that having only one
    provider's package installed is not an error.
    """
    provider = settings.llm_provider.strip().lower()

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            # None means api.openai.com. Anything else is another provider
            # that speaks the same API - see openai_base_url in config.py.
            base_url=settings.openai_base_url.strip() or None,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.llm_model,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    raise ValueError(
        f"unknown LLM_PROVIDER {settings.llm_provider!r} - use 'anthropic' or 'openai'"
    )


def active_model() -> str:
    """Provider and model currently configured. For logs and debugging."""
    provider = settings.llm_provider.strip().lower()
    model = settings.openai_model if provider == "openai" else settings.llm_model
    base = settings.openai_base_url.strip()
    if provider == "openai" and base:
        return f"{provider}:{model}@{base}"
    return f"{provider}:{model}"
