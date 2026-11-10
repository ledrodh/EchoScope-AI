"""Deterministic statistical metric calculations for YouTube channels and video performance."""

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd


@dataclass(slots=True)
class FormatBreakdown:
    shorts_count: int
    long_form_count: int
    unknown_count: int
    shorts_views: int
    long_form_views: int
    unknown_views: int


@dataclass(slots=True)
class ChannelSummaryMetrics:
    channel_id: str
    handle: str
    sample_size_videos: int  # n
    total_views: int
    average_views: float
    median_views: float
    p25_views: float
    p75_views: float
    views_to_subscribers_ratio: float | None  # VSR (Views / Subs)
    likes_to_views_ratio_pct: (
        float | None
    )  # (Sum Likes / Sum Views of videos with likes known) * 100
    likes_sample_size_videos: int  # Number of videos with visible likes
    upload_frequency_per_week: float
    average_views_per_day: float
    median_views_per_day: float
    format_breakdown: FormatBreakdown

    def to_analytical_payload(self) -> dict[str, dict[str, float | int | None]]:
        """Converts metrics into a canonical dictionary keyed by valid metric_id."""
        return {
            "median_views": {
                "value": self.median_views,
                "sample_size": self.sample_size_videos,
            },
            "average_views": {
                "value": self.average_views,
                "sample_size": self.sample_size_videos,
            },
            "p25_views": {
                "value": self.p25_views,
                "sample_size": self.sample_size_videos,
            },
            "p75_views": {
                "value": self.p75_views,
                "sample_size": self.sample_size_videos,
            },
            "views_to_subscribers_ratio": {
                "value": self.views_to_subscribers_ratio,
                "sample_size": self.sample_size_videos,
            },
            "likes_to_views_ratio_pct": {
                "value": self.likes_to_views_ratio_pct,
                "sample_size": self.likes_sample_size_videos,
            },
            "upload_frequency_per_week": {
                "value": self.upload_frequency_per_week,
                "sample_size": self.sample_size_videos,
            },
            "average_views_per_day": {
                "value": self.average_views_per_day,
                "sample_size": self.sample_size_videos,
            },
            "median_views_per_day": {
                "value": self.median_views_per_day,
                "sample_size": self.sample_size_videos,
            },
        }


def calculate_channel_metrics(
    videos_df: pd.DataFrame,
    channel_id: str,
    handle: str,
    subscribers_count: int | None,
    period_start: date,
    period_end: date,
    reference_collected_at: date | datetime | str | None = None,
) -> ChannelSummaryMetrics:
    """Computes transparent, defensible performance metrics for a channel's videos in a period.

    Ensures mathematical consistency:
    - Likes-to-views ratio only considers videos whose likeCount is exposed.
    - Views per day uses collected_at/snapshot reference date instead of local machine clock.
    - Both median and average VPD are reported for typical velocity assessment.
    """
    n = len(videos_df)

    # Resolve snapshot reference date
    if reference_collected_at is None:
        ref_date = period_end
    elif isinstance(reference_collected_at, datetime):
        ref_date = reference_collected_at.date()
    elif isinstance(reference_collected_at, str):
        ref_date = datetime.fromisoformat(reference_collected_at.replace("Z", "+00:00")).date()
    elif isinstance(reference_collected_at, date):
        ref_date = reference_collected_at
    else:
        ref_date = period_end

    # Empty dataset fallback
    if n == 0 or videos_df.empty:
        return ChannelSummaryMetrics(
            channel_id=channel_id,
            handle=handle,
            sample_size_videos=0,
            total_views=0,
            average_views=0.0,
            median_views=0.0,
            p25_views=0.0,
            p75_views=0.0,
            views_to_subscribers_ratio=None,
            likes_to_views_ratio_pct=None,
            likes_sample_size_videos=0,
            upload_frequency_per_week=0.0,
            average_views_per_day=0.0,
            median_views_per_day=0.0,
            format_breakdown=FormatBreakdown(0, 0, 0, 0, 0, 0),
        )

    records = videos_df.to_dict(orient="records")

    # 1. Central Tendency & Dispersion
    views_list = [float(str(r.get("views", 0))) for r in records]
    views_arr = np.array(views_list, dtype=float)
    total_views = int(np.sum(views_arr))
    avg_views = float(np.mean(views_arr))
    med_views = float(np.median(views_arr))
    p25 = float(np.percentile(views_arr, 25))
    p75 = float(np.percentile(views_arr, 75))

    # 2. Views-to-Subscriber Ratio (VSR)
    if subscribers_count is not None and subscribers_count > 0:
        vsr = round(float(total_views / subscribers_count), 4)
    else:
        vsr = None

    # 3. Likes-to-Views Ratio (Sample-consistent: Sum Likes / Sum Views of videos with likes known)
    records_with_likes = [
        r
        for r in records
        if r.get("likes") is not None
        and str(r.get("likes")).strip().lower() not in ("nan", "none", "")
    ]
    likes_sample_n = len(records_with_likes)
    if likes_sample_n > 0:
        likes_sum = sum(float(str(r["likes"])) for r in records_with_likes)
        views_with_likes_sum = sum(float(str(r.get("views", 0))) for r in records_with_likes)
        if views_with_likes_sum > 0:
            likes_views_ratio = round(float((likes_sum / views_with_likes_sum) * 100), 2)
        else:
            likes_views_ratio = None
    else:
        likes_views_ratio = None

    # 4. Upload Frequency (Videos per week across analysis window)
    period_days = max(1, (period_end - period_start).days + 1)
    weeks = period_days / 7.0
    upload_freq = round(n / weeks, 2)

    # 5. Views per Day (Historical daily rate based on collected_at reference date)
    daily_rates: list[float] = []
    for r in records:
        pub_val = r.get("date")
        if isinstance(pub_val, str):
            pub_date = datetime.fromisoformat(pub_val.replace("Z", "+00:00")).date()
        elif isinstance(pub_val, datetime):
            pub_date = pub_val.date()
        elif isinstance(pub_val, date):
            pub_date = pub_val
        else:
            pub_date = period_start

        days_alive = max(1, (ref_date - pub_date).days)
        vid_views = float(str(r.get("views", 0)))
        daily_rates.append(vid_views / days_alive)

    if daily_rates:
        avg_vpd = round(float(np.mean(daily_rates)), 2)
        med_vpd = round(float(np.median(daily_rates)), 2)
    else:
        avg_vpd = 0.0
        med_vpd = 0.0

    # 6. Format Breakdown (Explicitly tracking Shorts, Long-form, and Unknown)
    shorts_count = 0
    long_count = 0
    unknown_count = 0
    shorts_views = 0
    long_views = 0
    unknown_views = 0

    for r in records:
        cat = str(r.get("category", "Unknown"))
        v_count = int(str(r.get("views", 0)))
        if cat == "Shorts":
            shorts_count += 1
            shorts_views += v_count
        elif cat == "Long-form":
            long_count += 1
            long_views += v_count
        else:
            unknown_count += 1
            unknown_views += v_count

    breakdown = FormatBreakdown(
        shorts_count=shorts_count,
        long_form_count=long_count,
        unknown_count=unknown_count,
        shorts_views=shorts_views,
        long_form_views=long_views,
        unknown_views=unknown_views,
    )

    return ChannelSummaryMetrics(
        channel_id=channel_id,
        handle=handle,
        sample_size_videos=n,
        total_views=total_views,
        average_views=round(avg_views, 2),
        median_views=round(med_views, 2),
        p25_views=round(p25, 2),
        p75_views=round(p75, 2),
        views_to_subscribers_ratio=vsr,
        likes_to_views_ratio_pct=likes_views_ratio,
        likes_sample_size_videos=likes_sample_n,
        upload_frequency_per_week=upload_freq,
        average_views_per_day=avg_vpd,
        median_views_per_day=med_vpd,
        format_breakdown=breakdown,
    )


def compute_sample_market_share(
    channels_metrics: list[ChannelSummaryMetrics],
) -> tuple[int, dict[str, float]]:
    """Computes Share of Tracked Channel Views.

    Renamed from 'Market Share' to accurately indicate that calculations represent
    the monitored competitive sample, not the entire YouTube platform niche.

    Returns:
        (total_sample_views, {channel_handle: share_percentage})
    """
    total_sample_volume = sum(c.total_views for c in channels_metrics)
    if total_sample_volume == 0:
        return 0, {c.handle: 0.0 for c in channels_metrics}

    shares: dict[str, float] = {}
    for c in channels_metrics:
        pct = (c.total_views / total_sample_volume) * 100
        shares[c.handle] = round(pct, 2)

    return total_sample_volume, shares
