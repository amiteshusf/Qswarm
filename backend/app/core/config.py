"""Environment-driven configuration."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="qswarm-backend", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/qswarm",
        alias="DATABASE_URL",
    )

    jira_base_url: str = Field(default="", alias="JIRA_BASE_URL")
    jira_email: str = Field(default="", alias="JIRA_EMAIL")
    jira_api_token: str = Field(default="", alias="JIRA_API_TOKEN")
    jira_use_stub: bool = Field(default=True, alias="JIRA_USE_STUB")
    jira_default_test_reviewer_account_id: str = Field(
        default="",
        alias="JIRA_DEFAULT_TEST_REVIEWER_ACCOUNT_ID",
        description="Optional Atlassian accountId for assigning generated draft Jira Tasks.",
    )

    internal_actor_default: str = Field(default="system", alias="INTERNAL_ACTOR_DEFAULT")

    # coding / change planning (stub | codex later)
    coding_provider: str = Field(default="stub", alias="CODING_PROVIDER")

    # Playwright subprocess run (seconds)
    playwright_execution_timeout_seconds: int = Field(
        default=120,
        alias="PLAYWRIGHT_EXECUTION_TIMEOUT_SECONDS",
        ge=10,
        le=3600,
    )

    # GitHub PR creation (POST .../create-pr). Optional defaults when job has no repo_owner/repo_name.
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_default_repo_owner: str = Field(default="", alias="GITHUB_DEFAULT_REPO_OWNER")
    github_default_repo_name: str = Field(default="", alias="GITHUB_DEFAULT_REPO_NAME")
    github_api_base_url: str = Field(default="https://api.github.com", alias="GITHUB_API_BASE_URL")

    @field_validator("database_url", mode="before")
    @classmethod
    def coerce_database_url(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @property
    def jira_configured(self) -> bool:
        return bool(
            self.jira_base_url.strip()
            and self.jira_email.strip()
            and self.jira_api_token.strip()
        )

    @property
    def effective_jira_stub(self) -> bool:
        return self.jira_use_stub or not self.jira_configured


@lru_cache
def get_settings() -> Settings:
    return Settings()
