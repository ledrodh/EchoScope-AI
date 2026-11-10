"""Unit tests for deterministic channel metric calculations (Phase 4)."""

from datetime import date

import pandas as pd

from echoscope.analytics.metrics import calculate_channel_metrics, compute_sample_market_share


def test_metrics_odd_sample_median():
    """Validates median and percentiles on an odd number of videos."""
    df = pd.DataFrame(
        {
            "views": [1000, 2000, 9000],
            "likes": [100, 200, 900],
            "category": ["Shorts", "Long-form", "Unknown"],
            "date": ["2026-01-01", "2026-01-15", "2026-01-20"],
        }
    )

    metrics = calculate_channel_metrics(
        videos_df=df,
        channel_id="UC_ODD",
        handle="@OddChan",
        subscribers_count=10000,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        reference_collected_at=date(2026, 2, 1),
    )

    assert metrics.sample_size_videos == 3
    assert metrics.total_views == 12000
    assert metrics.average_views == 4000.0
    assert metrics.median_views == 2000.0  # Middle element of [1000, 2000, 9000]
    assert metrics.p25_views == 1500.0
    assert metrics.p75_views == 5500.0
    assert metrics.views_to_subscribers_ratio == 1.2  # 12000 / 10000
    assert metrics.likes_to_views_ratio_pct == 10.0  # (1200 / 12000) * 100
    assert metrics.likes_sample_size_videos == 3

    # Ensure Unknown format is explicitly tracked and visible
    assert metrics.format_breakdown.shorts_count == 1
    assert metrics.format_breakdown.long_form_count == 1
    assert metrics.format_breakdown.unknown_count == 1
    assert metrics.format_breakdown.unknown_views == 9000


def test_likes_to_views_ratio_sample_consistency():
    """CRITICAL FORMULA TEST: Only videos with known likes must enter the denominator.

    Vídeo A: 100,000 views, 5,000 likes
    Vídeo B: 50,000 views, Likes Unavailable (None)
    Vídeo C: 50,000 views, 2,000 likes
    Total views with likes: 150,000 (NOT 200,000!)
    Ratio: (7,000 / 150,000) * 100 = 4.67%
    """
    df = pd.DataFrame(
        {
            "views": [100000, 50000, 50000],
            "likes": [5000, None, 2000],
            "category": ["Long-form", "Long-form", "Long-form"],
            "date": ["2026-01-01", "2026-01-05", "2026-01-10"],
        }
    )

    metrics = calculate_channel_metrics(
        videos_df=df,
        channel_id="UC_LVR",
        handle="@LVRChan",
        subscribers_count=100000,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        reference_collected_at=date(2026, 2, 1),
    )

    assert metrics.likes_sample_size_videos == 2
    assert metrics.likes_to_views_ratio_pct == 4.67


def test_views_per_day_uses_reference_date_and_reports_median():
    """Ensures Views per Day is deterministic via reference_collected_at and reports median."""
    df = pd.DataFrame(
        {
            "views": [1000, 3000],
            "date": ["2026-01-01", "2026-01-11"],  # 30 days and 20 days alive as of 2026-01-31
        }
    )

    # Video 1: 1000 / 30 = 33.33
    # Video 2: 3000 / 20 = 150.0
    # Average: ~91.67, Median: 91.67
    metrics = calculate_channel_metrics(
        videos_df=df,
        channel_id="UC_VPD",
        handle="@VPDChan",
        subscribers_count=1000,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        reference_collected_at=date(2026, 1, 31),
    )

    assert metrics.average_views_per_day == 91.67
    assert metrics.median_views_per_day == 91.67


def test_metrics_handles_hidden_and_zero_subscribers():
    """Guarantees views-to-subscribers ratio is None when subscribers are hidden or zero."""
    df = pd.DataFrame(
        {"views": [5000], "likes": [250], "category": ["Long-form"], "date": ["2026-01-10"]}
    )

    # Hidden subscribers
    m_hidden = calculate_channel_metrics(
        df,
        "UC_H",
        "@Hidden",
        subscribers_count=None,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    assert m_hidden.views_to_subscribers_ratio is None

    # Zero subscribers
    m_zero = calculate_channel_metrics(
        df,
        "UC_Z",
        "@Zero",
        subscribers_count=0,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    assert m_zero.views_to_subscribers_ratio is None


def test_sample_market_share_calculation():
    """Validates Share of Tracked Channel Views and Total Analyzed Sample Volume."""
    df_a = pd.DataFrame({"views": [60000], "category": ["Long-form"], "date": ["2026-01-01"]})
    df_b = pd.DataFrame({"views": [40000], "category": ["Shorts"], "date": ["2026-01-01"]})

    m_a = calculate_channel_metrics(df_a, "UC_A", "@A", 10000, date(2026, 1, 1), date(2026, 1, 31))
    m_b = calculate_channel_metrics(df_b, "UC_B", "@B", 10000, date(2026, 1, 1), date(2026, 1, 31))

    total_vol, shares = compute_sample_market_share([m_a, m_b])

    assert total_vol == 100000
    assert shares["@A"] == 60.0
    assert shares["@B"] == 40.0


def test_metrics_zero_views_with_likes_branch():
    """Validates branch where videos have likes but views sum to zero."""
    df = pd.DataFrame(
        {
            "views": [0],
            "likes": [10],
            "category": ["Shorts"],
            "date": ["2026-01-05"],
        }
    )
    metrics = calculate_channel_metrics(
        videos_df=df,
        channel_id="UC_ZERO_V",
        handle="@ZeroV",
        subscribers_count=1000,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    assert metrics.likes_to_views_ratio_pct is None


def test_metrics_date_object_and_datetime_branches():
    """Validates pub_date as datetime, date, None, and reference_collected_at variations."""
    from datetime import UTC, datetime

    df = pd.DataFrame(
        {
            "views": [100, 200, 300],
            "likes": [None, "nan", ""],
            "category": ["Long-form", "Long-form", "Long-form"],
            "date": [
                datetime(2026, 1, 5, tzinfo=UTC),
                date(2026, 1, 10),
                None,  # Fallback branch: pub_date = period_start
            ],
        }
    )
    # Test reference_collected_at as datetime
    metrics_dt = calculate_channel_metrics(
        videos_df=df,
        channel_id="UC_DATES",
        handle="@Dates",
        subscribers_count=None,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        reference_collected_at=datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
    )
    assert metrics_dt.sample_size_videos == 3
    assert metrics_dt.likes_to_views_ratio_pct is None

    # Test reference_collected_at as None (defaults to period_end)
    metrics_none_ref = calculate_channel_metrics(
        videos_df=df,
        channel_id="UC_DATES",
        handle="@Dates",
        subscribers_count=None,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        reference_collected_at=None,
    )
    assert metrics_none_ref.average_views_per_day > 0


def test_sample_market_share_zero_volume_branch():
    """Validates compute_sample_market_share when all channels have 0 total views."""
    empty_df = pd.DataFrame()
    m1 = calculate_channel_metrics(
        empty_df, "UC_1", "@One", 100, date(2026, 1, 1), date(2026, 1, 31)
    )
    m2 = calculate_channel_metrics(
        empty_df, "UC_2", "@Two", 200, date(2026, 1, 1), date(2026, 1, 31)
    )

    total, shares = compute_sample_market_share([m1, m2])
    assert total == 0
    assert shares["@One"] == 0.0
    assert shares["@Two"] == 0.0
