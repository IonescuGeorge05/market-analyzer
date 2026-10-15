"""
Tests for the provider switch in app/llm.py.

Why this exists: the whole reason for going through LangChain instead of a
provider SDK is being able to change providers without touching the agents.
That claim is only worth making if switching is actually tested.

No network and no key: constructing a chat model does not call the API.
"""
import pytest

from app import llm
from app.config import settings


@pytest.fixture
def provider(monkeypatch):
    """Set LLM_PROVIDER for one test without touching the real .env."""
    def _set(name, **extra):
        monkeypatch.setattr(settings, "llm_provider", name)
        for k, v in extra.items():
            monkeypatch.setattr(settings, k, v)
    return _set


def test_anthropic_provider_builds_an_anthropic_client(provider):
    provider("anthropic", anthropic_api_key="sk-ant-test")
    from langchain_anthropic import ChatAnthropic
    assert isinstance(llm.chat(temperature=0, max_tokens=100), ChatAnthropic)


def test_openai_provider_builds_an_openai_client(provider):
    provider("openai", openai_api_key="sk-test")
    from langchain_openai import ChatOpenAI
    assert isinstance(llm.chat(temperature=0, max_tokens=100), ChatOpenAI)


def test_provider_name_is_case_and_whitespace_tolerant(provider):
    """A stray space or capital in .env should not break the app."""
    provider("  OpenAI  ", openai_api_key="sk-test")
    from langchain_openai import ChatOpenAI
    assert isinstance(llm.chat(temperature=0, max_tokens=100), ChatOpenAI)


def test_unknown_provider_fails_loudly(provider):
    """
    A typo in .env must not silently fall back to a provider you did not
    choose - you would be billed by the wrong company and not know why.
    """
    provider("gemini")
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        llm.chat(temperature=0, max_tokens=100)


def test_temperature_and_max_tokens_reach_the_client(provider):
    """The Analyzer asks for 0 and the Reporter for 0.3; both must land."""
    provider("openai", openai_api_key="sk-test")
    m = llm.chat(temperature=0.3, max_tokens=1200)
    assert m.temperature == 0.3
    assert m.max_tokens == 1200


def test_base_url_points_openai_client_at_another_provider(provider):
    """OPENAI_BASE_URL is how a free OpenAI-compatible provider is used."""
    url = "https://api.groq.com/openai/v1"
    provider("openai", openai_api_key="gsk-test", openai_base_url=url)
    assert llm.chat(temperature=0, max_tokens=100).openai_api_base == url


def test_blank_base_url_means_openai_itself(provider):
    provider("openai", openai_api_key="sk-test", openai_base_url="  ")
    assert llm.chat(temperature=0, max_tokens=100).openai_api_base is None


def test_reasoning_effort_is_sent_only_when_configured(provider):
    """gpt-4o-mini rejects the parameter, a thinking model needs it."""
    provider("openai", openai_api_key="sk-test", openai_reasoning_effort="")
    assert llm.chat(temperature=0, max_tokens=100).reasoning_effort is None
    provider("openai", openai_api_key="sk-test", openai_reasoning_effort="minimal")
    assert llm.chat(temperature=0, max_tokens=100).reasoning_effort == "minimal"


def test_active_model_reports_the_configured_pair(provider):
    provider("openai", openai_model="gpt-4o-mini", openai_base_url="")
    assert llm.active_model() == "openai:gpt-4o-mini"
    provider("anthropic", llm_model="claude-haiku-4-5-20251001")
    assert llm.active_model() == "anthropic:claude-haiku-4-5-20251001"


def test_agents_do_not_import_a_provider_directly():
    """
    Design rule 1 in spirit: the agents must go through the factory, so a
    provider change never means editing an agent.
    """
    from pathlib import Path
    agents = Path(__file__).resolve().parents[1] / "app" / "agents"
    for f in ("analyzer.py", "reporter.py"):
        src = (agents / f).read_text(encoding="utf-8")
        assert "langchain_anthropic" not in src, f"{f} imports a provider directly"
        assert "langchain_openai" not in src, f"{f} imports a provider directly"
