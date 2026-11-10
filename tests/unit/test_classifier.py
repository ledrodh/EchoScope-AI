"""Unit tests for YouTube video format classification (Phase 3)."""

from datetime import date

from echoscope.analytics.format_classifier import classify_video_format


def test_long_video_always_classified_as_long_form():
    """Videos with duration > 180s are always Long-form regardless of date or aspect ratio."""
    res_recent = classify_video_format(
        duration_seconds=181, published_at=date(2026, 1, 1), aspect_ratio="vertical"
    )
    assert res_recent.format_category == "Long-form"
    assert "exceeds maximum Shorts limit" in res_recent.classification_evidence

    res_old = classify_video_format(
        duration_seconds=600, published_at=date(2023, 1, 1), aspect_ratio=None
    )
    assert res_old.format_category == "Long-form"


def test_confirmed_horizontal_always_long_form():
    """Confirmed horizontal landscape videos are never Shorts even if duration <= 60s."""
    res = classify_video_format(
        duration_seconds=45, published_at=date(2026, 1, 1), aspect_ratio="horizontal"
    )
    assert res.format_category == "Long-form"
    assert "Confirmed horizontal aspect ratio" in res.classification_evidence


def test_pre_cutoff_rules_standard_channel():
    """Prior to Oct 15, 2024: Shorts must be <= 60s and vertical/square; >60s is Long-form."""
    old_date = date(2024, 5, 1)

    # 1. Duration > 60s pre-cutoff -> Long-form
    res_medium = classify_video_format(
        duration_seconds=90, published_at=old_date, aspect_ratio="vertical"
    )
    assert res_medium.format_category == "Long-form"
    assert "before Standard cutoff" in res_medium.classification_evidence

    # 2. Duration <= 60s with confirmed vertical/square -> Shorts
    res_vertical = classify_video_format(
        duration_seconds=50, published_at=old_date, aspect_ratio="vertical"
    )
    assert res_vertical.format_category == "Shorts"

    res_square = classify_video_format(
        duration_seconds=30, published_at=old_date, aspect_ratio="square"
    )
    assert res_square.format_category == "Shorts"

    # 3. Duration <= 60s with unknown orientation -> Unknown
    res_unknown = classify_video_format(
        duration_seconds=45, published_at=old_date, aspect_ratio=None
    )
    assert res_unknown.format_category == "Unknown"
    assert "public API does not expose aspect ratio" in res_unknown.classification_evidence


def test_post_cutoff_rules_standard_channel():
    """After Oct 15, 2024: videos up to 180s can be Shorts if vertical/square, otherwise Unknown/Long-form."""
    new_date = date(2025, 2, 1)

    # 1. 120s with confirmed vertical -> Shorts
    res_vertical = classify_video_format(
        duration_seconds=120, published_at=new_date, aspect_ratio="vertical"
    )
    assert res_vertical.format_category == "Shorts"
    assert (
        "Duration 120s <= 180s with confirmed vertical orientation"
        in res_vertical.classification_evidence
    )

    # 2. 120s with aspect ratio unexposed by public API -> Unknown
    res_unknown = classify_video_format(
        duration_seconds=120, published_at=new_date, aspect_ratio=None
    )
    assert res_unknown.format_category == "Unknown"

    # 3. 240s -> Long-form
    res_long = classify_video_format(
        duration_seconds=240, published_at=new_date, aspect_ratio="vertical"
    )
    assert res_long.format_category == "Long-form"


def test_official_artist_channel_cutoff_rule():
    """Official Artist Channels (OAC) have cutoff date on Dec 8, 2025."""
    between_date = date(
        2025, 6, 1
    )  # After standard cutoff (Oct 2024) but before artist cutoff (Dec 2025)
    after_date = date(2026, 1, 1)

    # 1. 90s on artist channel before Dec 2025 -> Long-form (rule expansion not yet in effect for OAC)
    res_oac_pre = classify_video_format(
        duration_seconds=90,
        published_at=between_date,
        aspect_ratio="vertical",
        is_artist_channel=True,
    )
    assert res_oac_pre.format_category == "Long-form"
    assert "OAC (Artist) cutoff" in res_oac_pre.classification_evidence

    # 2. 90s on artist channel after Dec 2025 -> Shorts
    res_oac_post = classify_video_format(
        duration_seconds=90,
        published_at=after_date,
        aspect_ratio="vertical",
        is_artist_channel=True,
    )
    assert res_oac_post.format_category == "Shorts"


def test_indeterminate_channel_type_returns_unknown_in_transition_window():
    """When OAC status is None, 61s-180s videos in the 2024-10-15..2025-12-08 window return Unknown."""
    # Ambiguous window -> Unknown
    res_ambiguous = classify_video_format(
        duration_seconds=90,
        published_at=date(2025, 6, 1),
        aspect_ratio="vertical",
        is_artist_channel=None,
    )
    assert res_ambiguous.format_category == "Unknown"
    assert res_ambiguous.classification_method == "indeterminate_channel_type"

    # Pre-Oct 15, 2024 (> 60s) -> universally Long-form even if OAC is None
    res_pre = classify_video_format(
        duration_seconds=90,
        published_at=date(2024, 5, 1),
        aspect_ratio="vertical",
        is_artist_channel=None,
    )
    assert res_pre.format_category == "Long-form"

    # Post-Dec 8, 2025 (<= 180s vertical) -> universally Shorts even if OAC is None
    res_post = classify_video_format(
        duration_seconds=90,
        published_at=date(2026, 2, 1),
        aspect_ratio="vertical",
        is_artist_channel=None,
    )
    assert res_post.format_category == "Shorts"
