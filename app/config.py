"""Central config. Reads from .env so no key is ever hardcoded."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    newsapi_key: str = ""
    alphavantage_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "market-sentiment-analyzer/0.1"

    # Tunables
    llm_model: str = "claude-haiku-4-5-20251001"
    news_limit: int = 15
    reddit_limit: int = 15
    subreddits: str = "stocks+investing+wallstreetbets"


settings = Settings()
