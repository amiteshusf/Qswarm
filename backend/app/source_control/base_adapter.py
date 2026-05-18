"""Abstract source-control adapter for PR / MR creation (provider-agnostic surface)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.db.models.repository_connection import RepositoryConnection


class SourceControlProviderAdapterBase(ABC):
    """Pluggable source-control provider; session orchestration stays provider-agnostic."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @abstractmethod
    def validate_config(self, connection: RepositoryConnection) -> bool:
        """Return True if connection is runnable; raise SourceControlConfigurationError on fatal issues."""

    @abstractmethod
    def get_default_branch(self, connection: RepositoryConnection) -> str:
        """Return default target branch name for PRs."""

    @abstractmethod
    def ensure_working_branch(
        self,
        *,
        repo_root: Path,
        source_branch: str,
        target_branch: str,
    ) -> None:
        """Ensure source branch exists from target and is checked out."""

    @abstractmethod
    def commit_workspace_changes(
        self,
        *,
        repo_root: Path,
        message: str,
    ) -> str:
        """Stage and commit; return commit sha."""

    @abstractmethod
    def push_branch(self, *, repo_root: Path, branch: str) -> None:
        """Push branch to default remote."""

    @abstractmethod
    def create_code_review_request(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        """Create PR/MR on provider; return normalized dict (external_id, external_url, ...)."""
