from functools import lru_cache
from math import ceil
from pathlib import Path

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    dry_run: bool = True

    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_name: str = "telegram_call_bot"
    telegram_session_dir: Path = Field(default=Path("sessions"))

    telegram_alert_bot_token: str | None = None
    telegram_alert_chat_id: str | None = None

    llm_provider: str = "openai"
    llm_api_key: str | None = None
    llm_model: str = "gpt-5.4-nano"
    llm_base_url: str | None = None
    llm_enabled: bool = True
    llm_review_enabled: bool = True
    llm_review_model: str = "gpt-5.4-mini"
    llm_review_confidence_threshold: float = 0.75
    llm_review_intents: str = "BUY_CALL,WARNING,SOLD,TAKE_PROFIT"
    llm_fallback_to_ollama: bool = True
    context_linking_enabled: bool = True
    context_link_window_seconds: int = 60
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_timeout_seconds: int = 60

    gmgn_api_key: str | None = None
    gmgn_cli_path: str | None = "./node_modules/.bin/gmgn-cli"
    gmgn_base_url: str | None = None
    dexscreener_base_url: str = "https://api.dexscreener.com"
    helius_api_key: str | None = None
    helius_rpc_url: str | None = None
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_agent_kit_enabled: bool = False

    database_url: str = "sqlite:///./data/app.db"
    store_market_snapshot_raw_json: bool = False
    store_security_snapshot_raw_json: bool = False

    paper_initial_balance_sol: float = 20.0
    paper_entry_size_sol: float = 0.5
    paper_daily_max_loss_sol: float = 2.0
    open_event_refresh_seconds: int = 300
    paper_fast_monitor_enabled: bool = True
    paper_fast_monitor_seconds: int = 5
    paper_fast_monitor_max_tokens: int = 30
    paper_closed_monitor_enabled: bool = True
    paper_closed_monitor_seconds: int = 900
    paper_closed_monitor_max_tokens: int = 30
    dexscreener_request_budget_per_minute: int = 240

    log_level: str = "INFO"
    channels_config_path: Path = Field(default=Path("config/channels.yaml"))
    strategy_config_path: Path = Field(default=Path("config/strategy.yaml"))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator(
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_alert_bot_token",
        "telegram_alert_chat_id",
        "llm_api_key",
        "llm_base_url",
        "gmgn_api_key",
        "gmgn_cli_path",
        "gmgn_base_url",
        "helius_api_key",
        "helius_rpc_url",
        mode="before",
    )
    @classmethod
    def blank_string_to_none(cls, value):
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_dexscreener_fast_monitor_budget(self):
        if self.context_link_window_seconds < 1:
            raise ValueError("CONTEXT_LINK_WINDOW_SECONDS must be at least 1")
        if self.paper_fast_monitor_seconds < 1:
            raise ValueError("PAPER_FAST_MONITOR_SECONDS must be at least 1")
        if not 1 <= self.paper_fast_monitor_max_tokens <= 30:
            raise ValueError("PAPER_FAST_MONITOR_MAX_TOKENS must be between 1 and 30")
        if self.paper_closed_monitor_seconds < 60:
            raise ValueError("PAPER_CLOSED_MONITOR_SECONDS must be at least 60")
        if not 1 <= self.paper_closed_monitor_max_tokens <= 30:
            raise ValueError("PAPER_CLOSED_MONITOR_MAX_TOKENS must be between 1 and 30")
        if not 1 <= self.dexscreener_request_budget_per_minute < 300:
            raise ValueError("DEXSCREENER_REQUEST_BUDGET_PER_MINUTE must be between 1 and 299")
        fast_requests_per_minute = ceil(60 / self.paper_fast_monitor_seconds)
        if fast_requests_per_minute > self.dexscreener_request_budget_per_minute:
            raise ValueError("Paper fast monitor frequency exceeds the DexScreener request budget")
        return self

    @property
    def real_trading_enabled(self) -> bool:
        return False

    @property
    def review_intents(self) -> set[str]:
        return {
            intent.strip().upper()
            for intent in self.llm_review_intents.split(",")
            if intent.strip()
        }

    def load_channels_config(self) -> dict:
        return _load_yaml_with_example_fallback(
            self.channels_config_path,
            Path("config/channels.example.yaml"),
        )

    def load_strategy_config(self) -> dict:
        return _load_yaml_with_example_fallback(
            self.strategy_config_path,
            Path("config/strategy.example.yaml"),
        )


def _load_yaml_with_example_fallback(path: Path, fallback_path: Path) -> dict:
    selected_path = path if path.exists() else fallback_path
    if not selected_path.exists():
        return {}
    with selected_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
