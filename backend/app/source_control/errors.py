"""Typed errors for source-control provider adapters (PR / MR creation)."""


class SourceControlProviderError(Exception):
    code: str = "source_control_provider_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class SourceControlConfigurationError(SourceControlProviderError):
    code = "source_control_configuration"


class SourceControlAuthError(SourceControlProviderError):
    code = "source_control_auth"


class SourceControlRepoError(SourceControlProviderError):
    code = "source_control_repo"


class SourceControlPushError(SourceControlProviderError):
    code = "source_control_push"


class SourceControlCreateRequestError(SourceControlProviderError):
    code = "source_control_create_request"


class UnsupportedSourceControlProviderError(SourceControlProviderError):
    code = "unsupported_source_control_provider"
