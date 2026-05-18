"""Placeholder adapters for non-GitHub providers (future milestones)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.db.models.repository_connection import RepositoryConnection
from app.source_control.base_adapter import SourceControlProviderAdapterBase
from app.source_control.errors import SourceControlConfigurationError


class _NotImplementedProvider(SourceControlProviderAdapterBase):
    def __init__(self, settings: Settings, *, name: str):
        self._settings = settings
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    def validate_config(self, connection: RepositoryConnection) -> bool:
        del connection
        raise SourceControlConfigurationError(
            f"{self._name} PR creation is not implemented yet.",
            code="source_control_configuration",
        )

    def get_default_branch(self, connection: RepositoryConnection) -> str:
        del connection
        raise SourceControlConfigurationError("not implemented", code="source_control_configuration")

    def ensure_working_branch(
        self,
        *,
        repo_root: Path,
        source_branch: str,
        target_branch: str,
    ) -> None:
        del repo_root, source_branch, target_branch
        raise SourceControlConfigurationError("not implemented", code="source_control_configuration")

    def commit_workspace_changes(
        self,
        *,
        repo_root: Path,
        message: str,
    ) -> str:
        del repo_root, message
        raise SourceControlConfigurationError("not implemented", code="source_control_configuration")

    def push_branch(self, *, repo_root: Path, branch: str) -> None:
        del repo_root, branch
        raise SourceControlConfigurationError("not implemented", code="source_control_configuration")

    def create_code_review_request(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict:
        del owner, repo, title, body, head, base
        raise SourceControlConfigurationError("not implemented", code="source_control_configuration")


class GitLabSourceControlAdapter(_NotImplementedProvider):
    def __init__(self, settings: Settings):
        super().__init__(settings, name="gitlab")


class BitbucketSourceControlAdapter(_NotImplementedProvider):
    def __init__(self, settings: Settings):
        super().__init__(settings, name="bitbucket")


class AzureDevOpsSourceControlAdapter(_NotImplementedProvider):
    def __init__(self, settings: Settings):
        super().__init__(settings, name="azure_devops")
