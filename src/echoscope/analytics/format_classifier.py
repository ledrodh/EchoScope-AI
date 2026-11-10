"""Deterministic video format classifier for YouTube content.

Applies YouTube official guidelines:
- Pre-October 15, 2024 (standard channels): Shorts up to 60s if vertical/square; >60s is Long-form.
- Post-October 15, 2024 (standard channels): Shorts up to 180s if vertical/square; >180s is Long-form.
- Official Artist Channels (OAC): Transition date cutoff is December 8, 2025.
- Unknown: Legitimate classification when aspect ratio cannot be determined from public API metadata.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from echoscope.database.models import FormatCategory

AspectRatio = Literal["vertical", "square", "horizontal"]

STANDARD_CUTOFF_DATE = date(2024, 10, 15)
ARTIST_CUTOFF_DATE = date(2025, 12, 8)


@dataclass(slots=True)
class FormatClassificationResult:
    format_category: FormatCategory
    classification_method: str
    classification_evidence: str


def _normalize_date(dt: date | datetime) -> date:
    if isinstance(dt, datetime):
        return dt.date()
    return dt


def classify_video_format(
    duration_seconds: int,
    published_at: date | datetime,
    aspect_ratio: AspectRatio | str | None = None,
    is_artist_channel: bool | None = False,
) -> FormatClassificationResult:
    """Classifies a video into 'Shorts', 'Long-form', or 'Unknown' based on official temporal rules.

    Args:
        duration_seconds: Video duration in seconds.
        published_at: Date or datetime when the video was published.
        aspect_ratio: Known orientation ('vertical', 'square', 'horizontal') or None if unexposed by API.
        is_artist_channel: Whether the channel is an Official Artist Channel (OAC), False if Standard,
            or None when OAC status cannot be verified from public API metadata.

    Returns:
        FormatClassificationResult with category, method, and explicit factual evidence.
    """
    pub_date = _normalize_date(published_at)

    # Rule 1: Videos exceeding 180 seconds are always Long-form across all channel types and dates
    if duration_seconds > 180:
        return FormatClassificationResult(
            format_category="Long-form",
            classification_method="duration_threshold",
            classification_evidence=f"Duration {duration_seconds}s exceeds maximum Shorts limit (180s)",
        )

    # Rule 2: Explicitly confirmed horizontal videos are Long-form regardless of short duration
    if aspect_ratio == "horizontal":
        return FormatClassificationResult(
            format_category="Long-form",
            classification_method="aspect_ratio_and_duration",
            classification_evidence="Confirmed horizontal aspect ratio (16:9 landscape)",
        )

    # Rule 3: When channel OAC status is indeterminate (None)
    if is_artist_channel is None:
        if pub_date >= ARTIST_CUTOFF_DATE:
            cutoff_date = ARTIST_CUTOFF_DATE
            channel_type_desc = "Universal post-OAC"
        elif pub_date < STANDARD_CUTOFF_DATE:
            cutoff_date = STANDARD_CUTOFF_DATE
            channel_type_desc = "Universal pre-expansion"
        else:
            # Ambiguous transition window (2024-10-15 .. 2025-12-07)
            if duration_seconds <= 60 and aspect_ratio in ("vertical", "square"):
                return FormatClassificationResult(
                    format_category="Shorts",
                    classification_method="universal_short_duration",
                    classification_evidence=(
                        f"Duration {duration_seconds}s <= 60s with confirmed {aspect_ratio} orientation"
                    ),
                )
            if duration_seconds > 60:
                return FormatClassificationResult(
                    format_category="Unknown",
                    classification_method="indeterminate_channel_type",
                    classification_evidence=(
                        f"Duration {duration_seconds}s (61s-180s) published on {pub_date} during OAC "
                        "transition window, and channel OAC status cannot be verified via public API"
                    ),
                )
            return FormatClassificationResult(
                format_category="Unknown",
                classification_method="indeterminate_aspect_ratio",
                classification_evidence=(
                    f"Duration {duration_seconds}s <= 60s published on {pub_date}, "
                    "and public API does not expose aspect ratio"
                ),
            )
    else:
        cutoff_date = ARTIST_CUTOFF_DATE if is_artist_channel else STANDARD_CUTOFF_DATE
        channel_type_desc = "OAC (Artist)" if is_artist_channel else "Standard"

    # Rule 3: Published BEFORE the applicable cutoff date
    if pub_date < cutoff_date:
        if duration_seconds > 60:
            return FormatClassificationResult(
                format_category="Long-form",
                classification_method="pre_cutoff_rule",
                classification_evidence=(
                    f"Duration {duration_seconds}s > 60s published on {pub_date} "
                    f"before {channel_type_desc} cutoff ({cutoff_date})"
                ),
            )
        # Duration <= 60s
        if aspect_ratio in ("vertical", "square"):
            return FormatClassificationResult(
                format_category="Shorts",
                classification_method="pre_cutoff_rule",
                classification_evidence=(
                    f"Duration {duration_seconds}s <= 60s with confirmed {aspect_ratio} orientation"
                ),
            )
        return FormatClassificationResult(
            format_category="Unknown",
            classification_method="indeterminate_aspect_ratio",
            classification_evidence=(
                f"Duration {duration_seconds}s <= 60s published on {pub_date} "
                "prior to rule expansion, but public API does not expose aspect ratio"
            ),
        )

    # Rule 4: Published AFTER the applicable cutoff date (duration <= 180s)
    if aspect_ratio in ("vertical", "square"):
        return FormatClassificationResult(
            format_category="Shorts",
            classification_method="post_cutoff_rule",
            classification_evidence=(
                f"Duration {duration_seconds}s <= 180s with confirmed {aspect_ratio} orientation "
                f"published after {cutoff_date}"
            ),
        )

    return FormatClassificationResult(
        format_category="Unknown",
        classification_method="indeterminate_aspect_ratio",
        classification_evidence=(
            f"Duration {duration_seconds}s <= 180s published on {pub_date} "
            f"after {cutoff_date}, but public API does not expose aspect ratio"
        ),
    )
