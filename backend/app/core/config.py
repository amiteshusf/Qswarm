"""Environment-driven configuration."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field(default="qswarm-backend", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    cors_extra_origins: str = Field(
        default="",
        alias="CORS_EXTRA_ORIGINS",
        description="Comma-separated extra browser origins allowed by CORS (in addition to qswarm-ui.vercel.app and localhost:5173).",
    )

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/qswarm",
        alias="DATABASE_URL",
    )
    database_echo: bool = Field(
        default=False,
        alias="DATABASE_ECHO",
        description="When true, SQLAlchemy logs SQL statements (independent of APP_DEBUG).",
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

    # Sprint 2 Phase 2 — Claude Code CLI (subprocess). See README for setup.
    qswarm_claude_code_enabled: bool = Field(default=False, alias="QSWARM_CLAUDE_CODE_ENABLED")
    qswarm_claude_code_command: str = Field(
        default="claude",
        alias="QSWARM_CLAUDE_CODE_COMMAND",
        description="Binary name or absolute path for the Claude Code CLI (no shell).",
    )
    qswarm_claude_code_extra_args: str = Field(
        default="",
        alias="QSWARM_CLAUDE_CODE_EXTRA_ARGS",
        description="Extra argv tokens (shlex-split), e.g. --verbose before the prompt flag.",
    )
    qswarm_claude_code_working_mode: str = Field(
        default="one_shot",
        alias="QSWARM_CLAUDE_CODE_WORKING_MODE",
        description="Reserved for future multi-step modes; currently informational only.",
    )
    qswarm_claude_code_allow_revision: bool = Field(
        default=True,
        alias="QSWARM_CLAUDE_CODE_ALLOW_REVISION",
    )
    qswarm_claude_code_timeout_seconds: int = Field(
        default=600,
        alias="QSWARM_CLAUDE_CODE_TIMEOUT_SECONDS",
        ge=10,
        le=7200,
    )
    qswarm_copilot_agent_enabled: bool = Field(default=False, alias="QSWARM_COPILOT_AGENT_ENABLED")
    qswarm_copilot_agent_command: str = Field(
        default="copilot",
        alias="QSWARM_COPILOT_AGENT_COMMAND",
        description="Binary name or absolute path for GitHub Copilot CLI (no shell).",
    )
    qswarm_copilot_agent_extra_args: str = Field(
        default="",
        alias="QSWARM_COPILOT_AGENT_EXTRA_ARGS",
        description=(
            "Extra Copilot CLI argv tokens (POSIX shlex-split) inserted after COMMAND and before "
            "-p/--prompt + task prompt. Hosted headless runs typically need write approval flags, "
            "e.g. --allow-tool=write --allow-all-paths (see GitHub Copilot CLI docs)."
        ),
    )
    qswarm_copilot_agent_allow_revision: bool = Field(
        default=True,
        alias="QSWARM_COPILOT_AGENT_ALLOW_REVISION",
    )
    qswarm_copilot_agent_timeout_seconds: int = Field(
        default=600,
        alias="QSWARM_COPILOT_AGENT_TIMEOUT_SECONDS",
        ge=10,
        le=7200,
    )

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
    qswarm_git_author_name: str = Field(
        default="",
        alias="QSWARM_GIT_AUTHOR_NAME",
        description="Commit author name for PR creation (repo-local git config, not global).",
    )
    qswarm_git_author_email: str = Field(
        default="",
        alias="QSWARM_GIT_AUTHOR_EMAIL",
        description="Commit author email for PR creation (repo-local git config, not global).",
    )

    # Managed workspaces for hosted session start (git clone under this root).
    qswarm_workspace_root: str = Field(default="/tmp/qswarm", alias="QSWARM_WORKSPACE_ROOT")
    qswarm_workspace_cache_ttl_minutes: int = Field(
        default=60,
        alias="QSWARM_WORKSPACE_CACHE_TTL_MINUTES",
        ge=5,
        le=10080,
        description="Idle TTL for cached hosted session workspaces (create-pr rebuild hints).",
    )
    qswarm_git_clone_timeout_seconds: int = Field(
        default=600,
        alias="QSWARM_GIT_CLONE_TIMEOUT_SECONDS",
        ge=30,
        le=7200,
    )
    qswarm_git_fetch_timeout_seconds: int = Field(
        default=120,
        alias="QSWARM_GIT_FETCH_TIMEOUT_SECONDS",
        ge=10,
        le=3600,
    )

    # npm ci / npm install after clone (session start) and optional revision paths.
    qswarm_bootstrap_timeout_seconds: int = Field(
        default=600,
        alias="QSWARM_BOOTSTRAP_TIMEOUT_SECONDS",
        ge=30,
        le=7200,
    )
    qswarm_skip_bootstrap_if_node_modules: bool = Field(
        default=True,
        alias="QSWARM_SKIP_BOOTSTRAP_IF_NODE_MODULES",
        description="When true, local_existing profile skips npm if node_modules is already populated.",
    )
    qswarm_playwright_browser_install_timeout_seconds: int = Field(
        default=900,
        alias="QSWARM_PLAYWRIGHT_BROWSER_INSTALL_TIMEOUT_SECONDS",
        ge=60,
        le=7200,
        description="Timeout for `npx playwright install chromium` on hosted materialized workspaces.",
    )

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
