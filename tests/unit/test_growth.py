"""Unit tests for Growth Signals analysis between snapshots (Phase 4)."""

from datetime import date

import pandas as pd

from echoscope.analytics.growth import compute_channel_growth


def test_growth_signals_comparable_videos_and_velocity():
    """Validates Delta V and daily growth velocity (R) on identical baseline videos."""
    df_t0 = pd.DataFrame(
        {
            "video_id": ["v1", "v2"],
            "title": ["Video 1", "Video 2"],
            "views": [10000, 20000],
            "date": ["2026-01-01", "2026-01-01"],
        }
    )
    # Measured 10 days later:
    # v1 gained +5,000 views (10,000 -> 15,000)
    # v2 gained +10,000 views (20,000 -> 30,000)
    df_t1 = pd.DataFrame(
        {
            "video_id": ["v1", "v2"],
            "title": ["Video 1", "Video 2"],
            "views": [15000, 30000],
            "date": ["2026-01-01", "2026-01-01"],
        }
    )

    growth = compute_channel_growth(
        channel_id="UC_GROWTH",
        run_id_older="run_t0",
        run_id_newer="run_t1",
        date_older=date(2026, 1, 1),
        date_newer=date(2026, 1, 11),  # 10 days
        videos_df_older=df_t0,
        videos_df_newer=df_t1,
    )

    assert growth.days_between_runs == 10
    assert growth.comparable_videos_count == 2
    assert growth.gross_positive_growth == 15000
    assert growth.negative_adjustments_views == 0
    assert growth.net_growth_views == 15000
    assert growth.daily_growth_velocity == 1500.0  # 15,000 / 10 days
    assert growth.overall_growth_rate_pct == 50.0  # (15,000 / 30,000) * 100
    assert growth.negative_adjustments_count == 0
    assert growth.new_videos_published_count == 0


def test_growth_signals_detects_negative_adjustments():
    """Identifies and counts when YouTube fraud/spam sweeps reduce a video's view count."""
    df_t0 = pd.DataFrame(
        {"video_id": ["v_sweep"], "title": ["Bot Video"], "views": [10000], "date": ["2026-01-01"]}
    )
    # Views adjusted downward by YouTube: 10,000 -> 9,500 (-500 views)
    df_t1 = pd.DataFrame(
        {"video_id": ["v_sweep"], "title": ["Bot Video"], "views": [9500], "date": ["2026-01-01"]}
    )

    growth = compute_channel_growth(
        channel_id="UC_SWEEP",
        run_id_older="r0",
        run_id_newer="r1",
        date_older=date(2026, 1, 1),
        date_newer=date(2026, 1, 6),  # 5 days
        videos_df_older=df_t0,
        videos_df_newer=df_t1,
    )

    assert growth.comparable_videos_count == 1
    assert growth.gross_positive_growth == 0
    assert growth.negative_adjustments_views == 500
    assert growth.net_growth_views == -500
    assert growth.negative_adjustments_count == 1
    assert growth.video_items[0].is_negative_adjustment


def test_growth_signals_distinguishes_truly_new_from_unmatched_old():
    """Ensures uploads published after T0 are marked as new, while pre-existing videos are not."""
    df_t0 = pd.DataFrame(
        {"video_id": ["v_base"], "title": ["Base Video"], "views": [1000], "date": ["2026-01-01"]}
    )
    # T1 has:
    # 1. v_base (gained 200 views)
    # 2. v_new (published 2026-01-05 > T0 2026-01-01 -> truly new upload!)
    # 3. v_missed_old (published 2025-12-01 < T0 -> historical video that was omitted in T0, not a new upload!)
    df_t1 = pd.DataFrame(
        {
            "video_id": ["v_base", "v_new", "v_missed_old"],
            "title": ["Base Video", "New Video", "Old Video"],
            "views": [1200, 5000, 8000],
            "date": ["2026-01-01", "2026-01-05", "2025-12-01"],
        }
    )

    growth = compute_channel_growth(
        channel_id="UC_DIST",
        run_id_older="r0",
        run_id_newer="r1",
        date_older=date(2026, 1, 1),
        date_newer=date(2026, 1, 10),
        videos_df_older=df_t0,
        videos_df_newer=df_t1,
    )

    assert growth.comparable_videos_count == 1
    assert growth.net_growth_views == 200
    assert growth.new_videos_published_count == 1  # Only v_new
    assert growth.new_videos_total_views == 5000
    assert growth.unmatched_historical_videos_count == 1  # v_missed_old


def test_growth_signals_empty_dataframes_and_datetime_objects():
    """Validates compute_channel_growth handles empty datasets and datetime date inputs gracefully."""
    from datetime import UTC, datetime

    dt_older = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    dt_newer = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)

    # Empty older and newer
    growth_empty = compute_channel_growth(
        channel_id="UC_EMPTY",
        run_id_older="r0",
        run_id_newer="r1",
        date_older=dt_older,
        date_newer=dt_newer,
        videos_df_older=pd.DataFrame(),
        videos_df_newer=pd.DataFrame(),
    )
    assert growth_empty.comparable_videos_count == 0
    assert growth_empty.daily_growth_velocity == 0.0
    assert growth_empty.overall_growth_rate_pct is None

    # Date formats in newer: datetime, date, None
    df_t1 = pd.DataFrame(
        {
            "video_id": ["v_dt", "v_d", "v_none"],
            "title": ["DT", "D", "None"],
            "views": [100, 200, 300],
            "date": [
                datetime(2026, 1, 10, tzinfo=UTC),  # new
                date(2026, 1, 12),  # new
                None,  # unmatched historical
            ],
        }
    )
    growth_dates = compute_channel_growth(
        channel_id="UC_DATES",
        run_id_older="r0",
        run_id_newer="r1",
        date_older=dt_older,
        date_newer=dt_newer,
        videos_df_older=pd.DataFrame(),
        videos_df_newer=df_t1,
    )
    assert growth_dates.new_videos_published_count == 2
    assert growth_dates.unmatched_historical_videos_count == 1


def test_growth_signals_fractional_seconds_and_rejects_non_positive_delta():
    """Validates exact (T1-T0)/86400 calculation and rejection when T1 <= T0."""
    from datetime import UTC, datetime

    import pytest

    df_t0 = pd.DataFrame(
        {"video_id": ["v1"], "title": ["V1"], "views": [1000], "date": ["2026-01-01"]}
    )
    df_t1 = pd.DataFrame(
        {"video_id": ["v1"], "title": ["V1"], "views": [4000], "date": ["2026-01-01"]}
    )

    # 36 hours apart = 1.5 days; +3000 views / 1.5 days = 2000.0 views/day
    t0 = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)
    growth = compute_channel_growth(
        channel_id="UC_FRAC",
        run_id_older="r0",
        run_id_newer="r1",
        date_older=t0,
        date_newer=t1,
        videos_df_older=df_t0,
        videos_df_newer=df_t1,
    )
    assert growth.days_between_runs == 1.5
    assert growth.daily_growth_velocity == 2000.0

    # Equal timestamps -> ValueError
    with pytest.raises(ValueError, match="must be strictly greater"):
        compute_channel_growth(
            channel_id="UC_EQ",
            run_id_older="r0",
            run_id_newer="r1",
            date_older=t0,
            date_newer=t0,
            videos_df_older=df_t0,
            videos_df_newer=df_t1,
        )

    # Inverted timestamps -> ValueError
    with pytest.raises(ValueError, match="must be strictly greater"):
        compute_channel_growth(
            channel_id="UC_INV",
            run_id_older="r0",
            run_id_newer="r1",
            date_older=t1,
            date_newer=t0,
            videos_df_older=df_t0,
            videos_df_newer=df_t1,
        )
