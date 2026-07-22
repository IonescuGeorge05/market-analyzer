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
    reddit_user_agent: str = "market-sentiment-analyzer/0.1"

    # Tunables
    # Which provider the agents talk to. See app/llm.py. Switching is a .env
    # change; the BUY/HOLD/SELL rule is computed in code either way.
    llm_provider: str = "anthropic"          # "anthropic" | "openai"
    llm_model: str = "claude-haiku-4-5-20251001"
    openai_model: str = "gpt-4o-mini"
    news_limit: int = 15
    reddit_limit: int = 15
    subreddits: str = "stocks+investing+wallstreetbets"


settings = Settings()
