"""Typed domain models for EchoScope relational storage."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

RunStatus = Literal["pending", "running", "completed", "failed", "partial"]
ChannelRunStatus = Literal["pending", "completed", "failed", "empty"]
FormatCategory = Literal["Shorts", "Long-form", "Unknown"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Channel:
    channel_id: str
    handle: str
    title: str
    first_seen_at: datetime = field(default_factory=_utcnow)


@dataclass(slots=True)
class AnalysisRun:
    run_id: str
    period_start: date
    period_end: date
    status: RunStatus = "pending"
    metrics_version: str = "1.0"
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None


@dataclass(slots=True)
class AnalysisRunChannel:
    run_id: str
    channel_id: str
    status: ChannelRunStatus = "pending"
    videos_collected: int = 0
    error_code: str | None = None


@dataclass(slots=True)
class ChannelSnapshot:
    run_id: str
    channel_id: str
    subscribers_count: int | None = None
    collected_at: datetime = field(default_factory=_utcnow)
    id: int | None = None


@dataclass(slots=True)
class Video:
    video_id: str
    channel_id: str
    title: str
    published_at: datetime
    duration_seconds: int


@dataclass(slots=True)
class VideoSnapshot:
    run_id: str
    channel_id: str
    video_id: str
    views_count: int
    format_category: FormatCategory
    likes_count: int | None = None
    classification_method: str = "duration_and_date"
    classification_evidence: str = ""
    collected_at: datetime = field(default_factory=_utcnow)
    id: int | None = None
