"""Resolve source-control provider id to adapter."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.constants import SourceControlProviderName
from app.db.models.repository_connection import RepositoryConnection
from app.source_control.errors import UnsupportedSourceControlProviderError
from app.source_control.base_adapter import SourceControlProviderAdapterBase
from app.source_control.github_provider_adapter import GitHubSourceControlAdapter
from app.source_control.placeholder_sc_providers import (
    AzureDevOpsSourceControlAdapter,
    BitbucketSourceControlAdapter,
    GitLabSourceControlAdapter,
)


def resolve_source_control_adapter(
    provider: str,
    *,
    settings: Settings | None = None,
) -> SourceControlProviderAdapterBase:
    try:
        name = SourceControlProviderName.parse(provider)
    except ValueError as e:
        raise UnsupportedSourceControlProviderError(str(e), code="unsupported_source_control_provider") from e

    s = settings or get_settings()
    if name == SourceControlProviderName.GITHUB:
        return GitHubSourceControlAdapter(s)
    if name == SourceControlProviderName.GITLAB:
        return GitLabSourceControlAdapter(s)
    if name == SourceControlProviderName.BITBUCKET:
        return BitbucketSourceControlAdapter(s)
    if name == SourceControlProviderName.AZURE_DEVOPS:
        return AzureDevOpsSourceControlAdapter(s)
    raise UnsupportedSourceControlProviderError(
        f"unsupported_source_control_provider:{name.value}", code="unsupported_source_control_provider"
    )
