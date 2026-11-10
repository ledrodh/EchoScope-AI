"""Resilient YouTube Data API v3 client with quota tracking and granular typed error handling."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import isodate
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from echoscope.analytics.format_classifier import classify_video_format
from echoscope.config import get_settings
from echoscope.database.models import Video, VideoSnapshot


# --- Granular Typed Domain Exceptions ---
class EchoScopeAPIError(Exception):
    """Base class for EchoScope API exceptions."""


class QuotaExceededError(EchoScopeAPIError):
    """Raised when YouTube API quota has been exhausted (quotaExceeded)."""


class RateLimitExceededError(EchoScopeAPIError):
    """Raised when temporary rate limit has been exceeded (rateLimitExceeded)."""


class InvalidAPIKeyError(EchoScopeAPIError):
    """Raised when YouTube API key is rejected as invalid (API_KEY_INVALID, keyInvalid)."""


class AccessNotConfiguredError(EchoScopeAPIError):
    """Raised when YouTube Data API v3 is not enabled in the Google Cloud Project (accessNotConfigured)."""


class AccessForbiddenError(EchoScopeAPIError):
    """Raised on general 403 forbidden without a specific key/quota classification."""


class ChannelNotFoundError(EchoScopeAPIError):
    """Raised when the requested YouTube channel cannot be found."""


class ChannelSuspendedError(EchoScopeAPIError):
    """Raised when a channel is closed or suspended (channelClosed, channelSuspended)."""


class YouTubeAPIUnavailableError(EchoScopeAPIError):
    """Raised when Google servers return 5xx errors or service is down."""


class NetworkError(EchoScopeAPIError):
    """Raised on socket timeouts or transport connectivity failures."""


def normalize_handle(handle: str) -> str:
    """Sanitizes user input into a clean YouTube handle.

    Strips leading '@', spaces, and validates against allowed handle characters.
    """
    clean = handle.strip().lstrip("@")
    if not clean:
        raise ValueError("Channel handle cannot be empty")
    if not re.match(r"^[a-zA-Z0-9_.-]+$", clean):
        raise ValueError(f"Invalid characters in channel handle: '{handle}'")
    return clean


@dataclass(slots=True)
class ResolvedChannel:
    channel_id: str
    handle: str
    title: str
    uploads_playlist_id: str
    subscribers_count: int | None
    is_artist_channel: bool = False


class YouTubeClient:
    """Manages YouTube Data API requests with quota estimation and robust pagination."""

    def __init__(self, api_key: str | None = None, service: Any = None):
        self.api_key = api_key or get_settings().youtube_api_key
        self.quota_units_used: int = 0
        if service is not None:
            self._service = service
        elif self.api_key:
            self._service = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
        else:
            self._service = None

    def _ensure_service(self) -> Any:
        if self._service is None:
            if not self.api_key:
                raise InvalidAPIKeyError(
                    "YouTube API key is not configured. Set YOUTUBE_API_KEY in .env."
                )
            self._service = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
        return self._service

    def _parse_error_reasons(self, err: HttpError) -> list[str]:
        """Extracts structured error reasons from the Google API error payload."""
        reasons: list[str] = []
        try:
            content_bytes = err.content if hasattr(err, "content") else b""
            if content_bytes:
                data = json.loads(content_bytes.decode("utf-8"))
                errors = data.get("error", {}).get("errors", [])
                for item in errors:
                    if "reason" in item:
                        reasons.append(item["reason"])
                # Also check top-level message if errors list is empty
                msg = data.get("error", {}).get("message", "")
                if msg:
                    reasons.append(msg)
        except Exception:
            pass
        return reasons

    def _handle_http_error(self, err: HttpError) -> None:
        """Parses Google API HttpError inspecting error.errors[].reason specifically."""
        status = err.resp.status
        reasons = self._parse_error_reasons(err)
        content_str = " ".join(reasons) if reasons else str(err)

        if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
            raise QuotaExceededError(
                "YouTube Data API quota exceeded. Try again tomorrow or use another project."
            ) from err
        if "rateLimitExceeded" in reasons or "userRateLimitExceeded" in reasons:
            raise RateLimitExceededError(
                "YouTube API temporary rate limit exceeded. Please back off and retry."
            ) from err
        if "accessNotConfigured" in reasons:
            raise AccessNotConfiguredError(
                "YouTube Data API v3 is not enabled in your Google Cloud Project console."
            ) from err
        if any(r in ("keyInvalid", "API_KEY_INVALID", "badRequest") for r in reasons) or (
            status == 400 and "API_KEY_INVALID" in content_str
        ):
            raise InvalidAPIKeyError("Invalid YouTube API key provided.") from err
        if "channelClosed" in reasons or "channelSuspended" in reasons:
            raise ChannelSuspendedError(
                "The requested YouTube channel has been closed or suspended."
            ) from err
        if status == 404 or "notFound" in reasons:
            raise ChannelNotFoundError("Resource not found on YouTube.") from err
        if status == 403:
            raise AccessForbiddenError(f"Access forbidden by YouTube API: {content_str}") from err
        if status >= 500:
            raise YouTubeAPIUnavailableError(
                "YouTube service is currently unavailable (5xx)."
            ) from err

        raise EchoScopeAPIError(f"YouTube API error (status {status}): {content_str}") from err

    def resolve_channel(self, channel_input: str) -> ResolvedChannel:
        """Resolves handle or channel ID into canonical metadata and uploads playlist."""
        service = self._ensure_service()
        clean = normalize_handle(channel_input)

        try:
            self.quota_units_used += 1
            if clean.startswith("UC") and len(clean) == 24:
                req = service.channels().list(
                    part="snippet,contentDetails,statistics,topicDetails", id=clean
                )
            else:
                req = service.channels().list(
                    part="snippet,contentDetails,statistics,topicDetails", forHandle=clean
                )
            res = req.execute()

            items = res.get("items", [])
            if not items and not clean.startswith("UC"):
                self.quota_units_used += 1
                req_fallback = service.channels().list(
                    part="snippet,contentDetails,statistics,topicDetails", forUsername=clean
                )
                res_fallback = req_fallback.execute()
                items = res_fallback.get("items", [])

            if not items:
                raise ChannelNotFoundError(f"Channel '{channel_input}' was not found on YouTube.")

            item = items[0]
            chan_id = item["id"]
            title = item["snippet"]["title"]
            custom_url = item["snippet"].get("customUrl", f"@{clean}")
            uploads_pl = item["contentDetails"]["relatedPlaylists"]["uploads"]

            # Detect Official Artist Channel (topicDetails or title badges if present)
            topics = item.get("topicDetails", {}).get("topicCategories", [])
            is_artist = any("music" in t.lower() for t in topics)

            # Respect hidden subscriber count policy
            stats = item.get("statistics", {})
            if stats.get("hiddenSubscriberCount", False):
                subscribers = None
            else:
                subscribers = int(stats["subscriberCount"]) if "subscriberCount" in stats else None

            return ResolvedChannel(
                channel_id=chan_id,
                handle=custom_url,
                title=title,
                uploads_playlist_id=uploads_pl,
                subscribers_count=subscribers,
                is_artist_channel=is_artist,
            )

        except HttpError as e:
            self._handle_http_error(e)
            raise
        except (TimeoutError, ConnectionError) as e:
            raise NetworkError(f"Network error while connecting to YouTube: {e}") from e

    def fetch_channel_videos_for_period(
        self,
        channel: ResolvedChannel,
        start_date: date,
        end_date: date,
        run_id: str,
        max_pages: int = 25,
    ) -> tuple[list[Video], list[VideoSnapshot]]:
        """Collects video details and statistics bounded within [start_date, end_date].

        Uses contentDetails.videoPublishedAt as primary publication timestamp.
        Implements temporal early-stopping with chronological sanity checks: if an out-of-order
        date is detected, early-break is disabled to prevent missing videos.
        """
        service = self._ensure_service()
        videos: list[Video] = []
        snapshots: list[VideoSnapshot] = []

        next_page_token: str | None = None
        keep_searching = True
        pages_traversed = 0
        last_seen_date: date | None = None
        chronological_order_trusted = True

        while keep_searching and pages_traversed < max_pages:
            pages_traversed += 1
            self.quota_units_used += 1

            try:
                pl_req = service.playlistItems().list(
                    part="snippet,contentDetails",
                    playlistId=channel.uploads_playlist_id,
                    maxResults=50,
                    pageToken=next_page_token,
                )
                pl_res = pl_req.execute()
            except HttpError as e:
                self._handle_http_error(e)
                raise
            except (TimeoutError, ConnectionError) as e:
                raise NetworkError(f"Network failure while fetching uploads: {e}") from e

            items = pl_res.get("items", [])
            if not items:
                break

            video_batch_ids: list[str] = []
            video_meta_cache: dict[str, dict[str, Any]] = {}

            for idx, item in enumerate(items):
                content_details = item.get("contentDetails", {})
                snippet = item.get("snippet", {})

                # MUST use contentDetails.videoPublishedAt (video publication date)
                # with fallback to snippet.publishedAt only if absent
                pub_date_str = content_details.get("videoPublishedAt") or snippet.get("publishedAt")
                if not pub_date_str:
                    continue

                vid_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                vid_date = vid_dt.date()

                # Verify chronological ordering
                if last_seen_date is not None and vid_date > last_seen_date:
                    chronological_order_trusted = False
                last_seen_date = vid_date

                # Date filtering: [start_date, end_date] inclusive
                if vid_date > end_date:
                    continue

                if vid_date < start_date:
                    # Check if any subsequent item in the current batch is actually >= start_date (out-of-order playlist)
                    has_future_items_in_batch = any(
                        (
                            datetime.fromisoformat(
                                (
                                    sub_item.get("contentDetails", {}).get("videoPublishedAt")
                                    or sub_item.get("snippet", {}).get(
                                        "publishedAt", "1970-01-01T00:00:00Z"
                                    )
                                ).replace("Z", "+00:00")
                            ).date()
                            >= start_date
                        )
                        for sub_item in items[idx + 1 :]
                        if (
                            sub_item.get("contentDetails", {}).get("videoPublishedAt")
                            or sub_item.get("snippet", {}).get("publishedAt")
                        )
                    )

                    if has_future_items_in_batch:
                        chronological_order_trusted = False
                        continue

                    if chronological_order_trusted:
                        # Safe to stop: all subsequent videos are confirmed older
                        keep_searching = False
                        break
                    continue

                vid_id = content_details.get("videoId")
                if not vid_id:
                    continue

                video_batch_ids.append(vid_id)
                video_meta_cache[vid_id] = {
                    "title": snippet.get("title", ""),
                    "published_at": vid_dt,
                }

            # Batch request statistics for valid IDs
            if video_batch_ids:
                self.quota_units_used += 1
                try:
                    stats_req = service.videos().list(
                        part="contentDetails,statistics",
                        id=",".join(video_batch_ids),
                    )
                    stats_res = stats_req.execute()
                except HttpError as e:
                    self._handle_http_error(e)
                    raise
                except (TimeoutError, ConnectionError) as e:
                    raise NetworkError(
                        f"Network failure while fetching video statistics: {e}"
                    ) from e

                for v_item in stats_res.get("items", []):
                    v_id = v_item["id"]
                    if v_id not in video_meta_cache:
                        continue

                    meta = video_meta_cache[v_id]
                    duration_iso = v_item["contentDetails"]["duration"]
                    duration_sec = int(isodate.parse_duration(duration_iso).total_seconds())

                    v_stats = v_item.get("statistics", {})
                    views_count = int(v_stats.get("viewCount", 0))
                    likes_count = int(v_stats["likeCount"]) if "likeCount" in v_stats else None

                    # Note: Third-party videos public API does NOT expose aspect_ratio.
                    # aspect_ratio=None is deliberately passed to produce legitimate 'Unknown'
                    # when <= 180s rather than making unverified guesses.
                    classification = classify_video_format(
                        duration_seconds=duration_sec,
                        published_at=meta["published_at"],
                        aspect_ratio=None,
                        is_artist_channel=channel.is_artist_channel,
                    )

                    videos.append(
                        Video(
                            video_id=v_id,
                            channel_id=channel.channel_id,
                            title=meta["title"],
                            published_at=meta["published_at"],
                            duration_seconds=duration_sec,
                        )
                    )

                    snapshots.append(
                        VideoSnapshot(
                            run_id=run_id,
                            channel_id=channel.channel_id,
                            video_id=v_id,
                            views_count=views_count,
                            likes_count=likes_count,
                            format_category=classification.format_category,
                            classification_method=classification.classification_method,
                            classification_evidence=classification.classification_evidence,
                            collected_at=datetime.now(UTC),
                        )
                    )

            next_page_token = pl_res.get("nextPageToken")
            if not next_page_token:
                keep_searching = False

        return videos, snapshots
