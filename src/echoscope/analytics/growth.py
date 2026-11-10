"""Growth Signals analysis tracking performance delta between distinct snapshot runs."""

from dataclasses import dataclass
from datetime import date, datetime
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
    days_between_runs: int
    comparable_videos_count: int
    gross_positive_growth: int
    negative_adjustments_views: int
    net_growth_views: int
    daily_growth_velocity: float  # R = Net Growth / Days
    overall_growth_rate_pct: float | None
    negative_adjustments_count: int
    new_videos_published_count: int
    new_videos_total_views: int
    unmatched_historical_videos_count: int
    video_items: list[VideoGrowthItem]


def compute_channel_growth(
    channel_id: str,
    run_id_older: str,
    run_id_newer: str,
    date_older: datetime | date,
    date_newer: datetime | date,
    videos_df_older: pd.DataFrame,
    videos_df_newer: pd.DataFrame,
) -> ChannelGrowthComparison:
    """Computes growth velocity (R) and Delta V between two point-in-time snapshots.

    Separates:
    - Gross positive growth vs negative view count adjustments.
    - True newly published uploads (published after date_older) vs historical videos missing from T0.
    """
    dt_older = date_older.date() if isinstance(date_older, datetime) else date_older
    dt_newer = date_newer.date() if isinstance(date_newer, datetime) else date_newer
    days_delta = max(1, (dt_newer - dt_older).days)

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
        if isinstance(pub_val, str):
            pub_date = datetime.fromisoformat(pub_val.replace("Z", "+00:00")).date()
        elif isinstance(pub_val, datetime):
            pub_date = pub_val.date()
        elif isinstance(pub_val, date):
            pub_date = pub_val
        else:
            pub_date = None

        if pub_date is not None and pub_date > dt_older:
            truly_new_ids.append(vid)
        else:
            unmatched_historical_count += 1

    new_videos_views = sum(int(dict_t1[vid]["views"]) for vid in truly_new_ids)

    return ChannelGrowthComparison(
        channel_id=channel_id,
        run_id_older=run_id_older,
        run_id_newer=run_id_newer,
        days_between_runs=days_delta,
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
