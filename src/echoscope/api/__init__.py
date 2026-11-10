"""YouTube API client package for EchoScope AI."""

from echoscope.api.youtube_client import (
    AccessForbiddenError,
    AccessNotConfiguredError,
    ChannelNotFoundError,
    ChannelSuspendedError,
    EchoScopeAPIError,
    InvalidAPIKeyError,
    NetworkError,
    QuotaExceededError,
    RateLimitExceededError,
    ResolvedChannel,
    YouTubeAPIUnavailableError,
    YouTubeClient,
    normalize_handle,
)

__all__ = [
    "AccessForbiddenError",
    "AccessNotConfiguredError",
    "ChannelNotFoundError",
    "ChannelSuspendedError",
    "EchoScopeAPIError",
    "InvalidAPIKeyError",
    "NetworkError",
    "QuotaExceededError",
    "RateLimitExceededError",
    "ResolvedChannel",
    "YouTubeAPIUnavailableError",
    "YouTubeClient",
    "normalize_handle",
]
