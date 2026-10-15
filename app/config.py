"""Central config. Reads from .env so no key is ever hardcoded."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    newsapi_key: str = ""
    alphavantage_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    # A Reddit app of type "script" authenticates with the developer account's
    # own username and password (the OAuth "password grant"). Without these,
    # PRAW falls back to app-only mode, which Reddit answers with 403 and an
    # HTML block page on data endpoints. Leave blank to stay app-only.
    reddit_username: str = ""
    reddit_password: str = ""
    reddit_user_agent: str = "market-sentiment-analyzer/0.1"

    # Tunables
    # Which provider the agents talk to. See app/llm.py. Switching is a .env
    # change; the BUY/HOLD/SELL rule is computed in code either way.
    llm_provider: str = "anthropic"          # "anthropic" | "openai"
    llm_model: str = "claude-haiku-4-5-20251001"
    openai_model: str = "gpt-4o-mini"
    # Point the "openai" provider at any OpenAI-compatible API instead: Gemini,
    # Groq, OpenRouter, DeepSeek, or a local Ollama. Blank means OpenAI itself.
    # Set OPENAI_API_KEY and OPENAI_MODEL to that provider's key and model.
    openai_base_url: str = ""
    # For "thinking" models (Gemini 3.x, OpenAI o-series). Hidden reasoning
    # tokens count against max_tokens: on gemini-3.5-flash at its default
    # effort, the Reporter spent ~1080 of 1200 tokens thinking and its JSON
    # was cut off mid-string. Neither scoring headlines nor a 150-word note
    # needs it, so "minimal" is right there. Blank sends nothing, which
    # non-reasoning models such as gpt-4o-mini require.
    openai_reasoning_effort: str = ""
    news_limit: int = 15
    reddit_limit: int = 15
    subreddits: str = "stocks+investing+wallstreetbets"


settings = Settings()
