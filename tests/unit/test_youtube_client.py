"""Unit tests for YouTube Data API client and error handling (Phase 3)."""

from datetime import date
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from echoscope.api.youtube_client import (
    AccessForbiddenError,
    AccessNotConfiguredError,
    ChannelNotFoundError,
    ChannelSuspendedError,
    InvalidAPIKeyError,
    NetworkError,
    QuotaExceededError,
    RateLimitExceededError,
    ResolvedChannel,
    YouTubeClient,
    normalize_handle,
)


def test_normalize_handle_validations():
    """Validates handle cleaning and boundary constraints."""
    assert normalize_handle("@TechLead") == "TechLead"
    assert normalize_handle("  mrbeast  ") == "mrbeast"
    assert normalize_handle("@channel_01.official-br") == "channel_01.official-br"

    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_handle("   ")

    with pytest.raises(ValueError, match="Invalid characters"):
        normalize_handle("invalid handle with spaces")


def test_resolve_channel_success():
    """Ensures channel lookup parses snippets, playlist ID, and subscriber counts correctly."""
    mock_service = MagicMock()
    mock_service.channels().list().execute.return_value = {
        "items": [
            {
                "id": "UC_TEST_123",
                "snippet": {"title": "Test Channel", "customUrl": "@TestChannel"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST_123"}},
                "statistics": {"subscriberCount": "1500000", "hiddenSubscriberCount": False},
                "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Technology"]},
            }
        ]
    }

    client = YouTubeClient(service=mock_service)
    resolved = client.resolve_channel("@TestChannel")

    assert resolved.channel_id == "UC_TEST_123"
    assert resolved.handle == "@TestChannel"
    assert resolved.title == "Test Channel"
    assert resolved.uploads_playlist_id == "UU_TEST_123"
    assert resolved.subscribers_count == 1500000
    assert not resolved.is_artist_channel
    assert client.quota_units_used == 1


def test_resolve_channel_artist_detection():
    """Validates that Official Artist Channels are identified via topicCategories."""
    mock_service = MagicMock()
    mock_service.channels().list().execute.return_value = {
        "items": [
            {
                "id": "UC_ARTIST_123",
                "snippet": {"title": "Artist Channel", "customUrl": "@Artist"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU_ARTIST_123"}},
                "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Music"]},
            }
        ]
    }

    client = YouTubeClient(service=mock_service)
    resolved = client.resolve_channel("@Artist")
    assert resolved.is_artist_channel


def test_resolve_channel_hidden_subscribers():
    """Validates that hidden subscriber count preserves None instead of masking with dummy values."""
    mock_service = MagicMock()
    mock_service.channels().list().execute.return_value = {
        "items": [
            {
                "id": "UC_HIDDEN_123",
                "snippet": {"title": "Hidden Subs Channel"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU_HIDDEN_123"}},
                "statistics": {"hiddenSubscriberCount": True},
            }
        ]
    }

    client = YouTubeClient(service=mock_service)
    resolved = client.resolve_channel("@HiddenSubs")
    assert resolved.subscribers_count is None


def test_resolve_channel_not_found():
    """Checks that empty items list raises ChannelNotFoundError."""
    mock_service = MagicMock()
    mock_service.channels().list().execute.return_value = {"items": []}

    client = YouTubeClient(service=mock_service)
    with pytest.raises(ChannelNotFoundError):
        client.resolve_channel("@NonExistent")


def test_video_published_at_vs_playlist_published_at():
    """Guarantees that contentDetails.videoPublishedAt takes precedence over snippet.publishedAt."""
    mock_service = MagicMock()
    mock_service.playlistItems().list().execute.return_value = {
        "items": [
            {
                "contentDetails": {
                    "videoId": "v_date_check",
                    "videoPublishedAt": "2026-01-15T10:00:00Z",
                },
                "snippet": {
                    "publishedAt": "2026-03-01T10:00:00Z",
                    "title": "Added Later to Playlist",
                },
            }
        ],
        "nextPageToken": None,
    }
    mock_service.videos().list().execute.return_value = {
        "items": [
            {
                "id": "v_date_check",
                "contentDetails": {"duration": "PT3M"},
                "statistics": {"viewCount": "100"},
            }
        ]
    }

    client = YouTubeClient(service=mock_service)
    channel = ResolvedChannel("UC_1", "@Ch", "Ch", "UU_1", 1000)

    # Window covers Jan 1 to Jan 31.
    # If it used snippet.publishedAt (March 1), it would skip!
    # Because it uses videoPublishedAt (Jan 15), it must be included.
    videos, snapshots = client.fetch_channel_videos_for_period(
        channel=channel, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), run_id="r1"
    )
    assert len(videos) == 1
    assert videos[0].video_id == "v_date_check"


def test_out_of_order_pagination_safeguard():
    """Verifies that out-of-order dates disable early-break, ensuring no valid videos are skipped."""
    mock_service = MagicMock()

    # Out-of-order items:
    # 1. 2026-01-20 (Inside window)
    # 2. 2025-12-01 (Older than start_date: 2026-01-01)
    # 3. 2026-01-10 (Inside window, but appeared AFTER older item!)
    mock_service.playlistItems().list().execute.return_value = {
        "items": [
            {
                "contentDetails": {"videoId": "v1", "videoPublishedAt": "2026-01-20T12:00:00Z"},
                "snippet": {"title": "V1"},
            },
            {
                "contentDetails": {"videoId": "v_old", "videoPublishedAt": "2025-12-01T12:00:00Z"},
                "snippet": {"title": "Old"},
            },
            {
                "contentDetails": {"videoId": "v2", "videoPublishedAt": "2026-01-10T12:00:00Z"},
                "snippet": {"title": "V2"},
            },
        ],
        "nextPageToken": None,
    }
    mock_service.videos().list().execute.return_value = {
        "items": [
            {
                "id": "v1",
                "contentDetails": {"duration": "PT60S"},
                "statistics": {"viewCount": "500"},
            },
            {
                "id": "v2",
                "contentDetails": {"duration": "PT60S"},
                "statistics": {"viewCount": "800"},
            },
        ]
    }

    client = YouTubeClient(service=mock_service)
    channel = ResolvedChannel("UC_1", "@Ch", "Ch", "UU_1", 1000)

    videos, snapshots = client.fetch_channel_videos_for_period(
        channel=channel, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), run_id="r1"
    )

    # If naive break was active, v2 would have been skipped! With safeguard, both v1 and v2 are collected.
    assert len(videos) == 2
    collected_ids = {v.video_id for v in videos}
    assert "v1" in collected_ids
    assert "v2" in collected_ids


def test_granular_http_exceptions():
    """Validates precise error reason parsing across Google API error payloads."""
    mock_service = MagicMock()
    client = YouTubeClient(service=mock_service)

    def _make_error(status, reason_code):
        resp = httplib2.Response({"status": status})
        payload = f'{{"error": {{"errors": [{{"reason": "{reason_code}"}}]}}}}'.encode()
        return HttpError(resp, payload)

    # 1. Quota Exceeded
    mock_service.channels().list().execute.side_effect = _make_error(403, "quotaExceeded")
    with pytest.raises(QuotaExceededError):
        client.resolve_channel("@Ch")

    # 2. Rate Limit Exceeded
    mock_service.channels().list().execute.side_effect = _make_error(403, "rateLimitExceeded")
    with pytest.raises(RateLimitExceededError):
        client.resolve_channel("@Ch")

    # 3. Access Not Configured (API disabled in GCP)
    mock_service.channels().list().execute.side_effect = _make_error(403, "accessNotConfigured")
    with pytest.raises(AccessNotConfiguredError):
        client.resolve_channel("@Ch")

    # 4. Invalid API Key
    mock_service.channels().list().execute.side_effect = _make_error(400, "keyInvalid")
    with pytest.raises(InvalidAPIKeyError):
        client.resolve_channel("@Ch")

    # 5. Channel Suspended / Closed
    mock_service.channels().list().execute.side_effect = _make_error(404, "channelClosed")
    with pytest.raises(ChannelSuspendedError):
        client.resolve_channel("@Ch")

    # 6. General 403 Forbidden (not key, not quota)
    mock_service.channels().list().execute.side_effect = _make_error(
        403, "someOtherForbiddenReason"
    )
    with pytest.raises(AccessForbiddenError):
        client.resolve_channel("@Ch")

    # 7. Network / Socket failure
    mock_service.channels().list().execute.side_effect = TimeoutError("Connection timed out")
    with pytest.raises(NetworkError):
        client.resolve_channel("@Ch")
