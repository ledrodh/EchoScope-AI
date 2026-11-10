"""Growth Signals analysis tracking performance delta between distinct snapshot runs."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd


@dataclass(slots=True)
class VideoGrowthItem:
    video_id: str
    title: str
    views_t0: int
    views_t1: int
    delta_views: int
    daily_velocity: float
    is_negative_adjustment: bool


@dataclass(slots=True)
class ChannelGrowthComparison:
    channel_id: str
    run_id_older: str
    run_id_newer: str
    days_between_runs: float
    comparable_videos_count: int
    gross_positive_growth: int
    negative_adjustments_views: int
    net_growth_views: int
    daily_growth_velocity: float  # R = Net Growth / Delta_t (in days)
    overall_growth_rate_pct: float | None
    negative_adjustments_count: int
    new_videos_published_count: int
    new_videos_total_views: int
    unmatched_historical_videos_count: int
    video_items: list[VideoGrowthItem]


def _parse_snapshot_timestamp(val: datetime | date | str) -> datetime:
    """Normalizes snapshot timestamps into timezone-aware UTC datetimes."""
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time(), tzinfo=UTC)
    if isinstance(val, str):
        parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise ValueError(f"Unsupported snapshot timestamp format: {val!r}")


def compute_channel_growth(
    channel_id: str,
    run_id_older: str,
    run_id_newer: str,
    date_older: datetime | date | str,
    date_newer: datetime | date | str,
    videos_df_older: pd.DataFrame,
    videos_df_newer: pd.DataFrame,
) -> ChannelGrowthComparison:
    """Computes growth velocity (R) and Delta V between two point-in-time snapshots.

    Uses actual collection timestamps (T1 - T0) / 86400 seconds and rejects T1 <= T0.
    Separates:
    - Gross positive growth vs negative view count adjustments.
    - True newly published uploads (published after date_older) vs historical videos missing from T0.
    """
    dt_older = _parse_snapshot_timestamp(date_older)
    dt_newer = _parse_snapshot_timestamp(date_newer)

    delta_seconds = (dt_newer - dt_older).total_seconds()
    if delta_seconds <= 0:
        raise ValueError(
            f"Newer snapshot timestamp ({dt_newer.isoformat()}) must be strictly greater than "
            f"older snapshot timestamp ({dt_older.isoformat()})."
        )

    days_delta = delta_seconds / 86400.0

    dict_t0: dict[str, dict[str, Any]] = {}
    if not videos_df_older.empty and "video_id" in videos_df_older.columns:
        for item in videos_df_older.to_dict(orient="records"):
            v_id = str(item["video_id"])
            dict_t0[v_id] = {
                "views": int(str(item.get("views", 0))),
                "title": str(item.get("title", "")),
                "date": item.get("date"),
            }

    dict_t1: dict[str, dict[str, Any]] = {}
    if not videos_df_newer.empty and "video_id" in videos_df_newer.columns:
        for item in videos_df_newer.to_dict(orient="records"):
            v_id = str(item["video_id"])
            dict_t1[v_id] = {
                "views": int(str(item.get("views", 0))),
                "title": str(item.get("title", "")),
                "date": item.get("date"),
            }

    # 1. Compare identical videos existing in both snapshots
    video_items: list[VideoGrowthItem] = []
    gross_positive = 0
    negative_adjustments_views = 0
    negative_adjustments_count = 0
    t0_sum_comparable = 0

    common_ids = set(dict_t0.keys()).intersection(dict_t1.keys())

    for vid in common_ids:
        v0 = int(dict_t0[vid]["views"])
        v1 = int(dict_t1[vid]["views"])
        delta = v1 - v0
        is_neg = delta < 0

        if is_neg:
            negative_adjustments_count += 1
            negative_adjustments_views += abs(delta)
        else:
            gross_positive += delta

        velocity = round(delta / days_delta, 2)
        t0_sum_comparable += v0

        title_str = str(dict_t1[vid]["title"] or dict_t0[vid]["title"])
        video_items.append(
            VideoGrowthItem(
                video_id=vid,
                title=title_str,
                views_t0=v0,
                views_t1=v1,
                delta_views=delta,
                daily_velocity=velocity,
                is_negative_adjustment=is_neg,
            )
        )

    # Sort items by highest absolute delta descending
    video_items.sort(key=lambda x: x.delta_views, reverse=True)

    net_growth = gross_positive - negative_adjustments_views
    daily_velocity = round(net_growth / days_delta, 2)
    growth_rate = (
        round((net_growth / t0_sum_comparable) * 100, 2) if t0_sum_comparable > 0 else None
    )

    # 2. Distinguish truly new uploads from unmatched historical videos
    extra_in_t1 = set(dict_t1.keys()).difference(dict_t0.keys())
    truly_new_ids: list[str] = []
    unmatched_historical_count = 0

    for vid in extra_in_t1:
        pub_val = dict_t1[vid].get("date")
        is_new_upload = False
        if isinstance(pub_val, datetime):
            pub_dt = pub_val if pub_val.tzinfo is not None else pub_val.replace(tzinfo=UTC)
            is_new_upload = pub_dt > dt_older
        elif isinstance(pub_val, date):
            is_new_upload = pub_val > dt_older.date()
        elif isinstance(pub_val, str) and pub_val.strip():
            raw_str = pub_val.strip()
            if len(raw_str) > 10 or "T" in raw_str or " " in raw_str:
                pub_dt = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=UTC)
                is_new_upload = pub_dt > dt_older
            else:
                pub_d = date.fromisoformat(raw_str[:10])
                is_new_upload = pub_d > dt_older.date()

        if is_new_upload:
            truly_new_ids.append(vid)
        else:
            unmatched_historical_count += 1

    new_videos_views = sum(int(dict_t1[vid]["views"]) for vid in truly_new_ids)

    return ChannelGrowthComparison(
        channel_id=channel_id,
        run_id_older=run_id_older,
        run_id_newer=run_id_newer,
        days_between_runs=round(days_delta, 4),
        comparable_videos_count=len(common_ids),
        gross_positive_growth=gross_positive,
        negative_adjustments_views=negative_adjustments_views,
        net_growth_views=net_growth,
        daily_growth_velocity=daily_velocity,
        overall_growth_rate_pct=growth_rate,
        negative_adjustments_count=negative_adjustments_count,
        new_videos_published_count=len(truly_new_ids),
        new_videos_total_views=new_videos_views,
        unmatched_historical_videos_count=unmatched_historical_count,
        video_items=video_items,
    )
