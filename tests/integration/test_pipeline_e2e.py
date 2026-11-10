"""End-to-end integration tests validating multi-channel pipeline execution, analytics, and AI reports."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from echoscope.ai.schemas import (
    EchoScopeReport,
    MetricReference,
    PerformancePattern,
    StrategicHypothesis,
)
from echoscope.api.youtube_client import (
    NetworkError,
    ResolvedChannel,
    YouTubeClient,
)
from echoscope.database.connection import configure_sqlite_connection
from echoscope.database.models import Video, VideoSnapshot
from echoscope.services.pipeline_service import PipelineService


@pytest.fixture
def test_db():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    return configure_sqlite_connection(conn)


def test_pipeline_e2e_full_lifecycle(test_db):
    """End-to-end pipeline integration testing 3 channels:

    - Channel A: 20 videos successfully fetched.
    - Channel B: 15 videos successfully fetched.
    - Channel C: network failure (NetworkError).

    Validates:
    - 35 videos and snapshots persisted in SQLite.
    - Run status is 'partial' with granular channel error logging.
    - Deterministic analytics calculation for Channels A and B.
    - Share of Tracked Channel Views calculation.
    - Gemini strategic report generation and schema compliance.
    - 100% offline retrieval via get_run_overview() without external API requests.
    """
    mock_youtube = MagicMock(spec=YouTubeClient)
    mock_youtube.quota_units_used = 42

    def _mock_resolve(ch_input: str) -> ResolvedChannel:
        clean = ch_input.strip().lstrip("@")
        if "failing" in clean.lower():
            raise NetworkError(f"Connection timeout to YouTube for channel: {ch_input}")
        if "alpha" in clean.lower():
            return ResolvedChannel(
                channel_id="UC_ALPHA",
                handle="@AlphaTech",
                title="Alpha Tech",
                uploads_playlist_id="UU_ALPHA",
                subscribers_count=200000,
                is_artist_channel=False,
            )
        if "beta" in clean.lower():
            return ResolvedChannel(
                channel_id="UC_BETA",
                handle="@BetaGaming",
                title="Beta Gaming",
                uploads_playlist_id="UU_BETA",
                subscribers_count=150000,
                is_artist_channel=False,
            )
        raise ValueError(f"Unexpected channel: {ch_input}")

    def _mock_fetch(channel, start_date, end_date, run_id, max_pages=25):
        num_videos = 20 if "ALPHA" in channel.channel_id else 15
        base_views = 25000 if "ALPHA" in channel.channel_id else 18000
        videos = []
        snapshots = []

        for i in range(1, num_videos + 1):
            v_id = f"vid_{channel.channel_id}_{i}"
            views = base_views + (i * 1200)
            likes = int(views * 0.05)
            category = "Shorts" if i % 4 == 0 else "Long-form"
            duration = 45 if category == "Shorts" else 600

            v = Video(
                video_id=v_id,
                channel_id=channel.channel_id,
                title=f"{channel.title} - Video #{i}",
                published_at=datetime(2026, 1, min(i, 28), tzinfo=UTC),
                duration_seconds=duration,
            )
            s = VideoSnapshot(
                id=None,
                run_id=run_id,
                channel_id=channel.channel_id,
                video_id=v_id,
                views_count=views,
                likes_count=likes,
                format_category=category,
                classification_method="duration_and_date",
                classification_evidence=f"Duration: {duration}s",
                collected_at=datetime.now(UTC),
            )
            videos.append(v)
            snapshots.append(s)

        return videos, snapshots

    mock_youtube.resolve_channel.side_effect = _mock_resolve
    mock_youtube.fetch_channel_videos_for_period.side_effect = _mock_fetch

    mock_ai = MagicMock()

    def _mock_ai_generate(channel_metrics, top_videos):
        return EchoScopeReport(
            channel_handle=channel_metrics.handle,
            prompt_version="1.0.0",
            model_used="gemini-3.8-flash",
            observed_data=[
                MetricReference(
                    metric_id="median_views",
                    interpretation=f"Typical performance is {channel_metrics.median_views:,.0f} views across {channel_metrics.sample_size_videos} videos.",
                ),
                MetricReference(
                    metric_id="views_to_subscribers_ratio",
                    interpretation=f"Views-to-subscriber ratio is {channel_metrics.views_to_subscribers_ratio:.2f}.",
                ),
            ],
            performance_patterns=[
                PerformancePattern(
                    pattern_title="Consistent Long-Form Reach",
                    observation="Channel sustains above-average views across long-form publications.",
                    supporting_metric_ids=["median_views", "average_views"],
                )
            ],
            strategic_hypotheses=[
                StrategicHypothesis(
                    statement="Increasing weekly upload frequency will compound audience acquisition.",
                    supporting_metrics=["upload_frequency_per_week"],
                    limitations=["Private CTR and impression data are unknown"],
                    confidence_level="high",
                )
            ],
            study_limitations=["Analysis strictly reflects the 30-day monitoring window."],
        )

    mock_ai.generate_report.side_effect = _mock_ai_generate

    service = PipelineService(
        conn=test_db,
        youtube_client=mock_youtube,
        ai_client=mock_ai,
    )

    # 1. Initialize execution run
    run_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    assert run_id.startswith("run_")

    # 2. Execute collection across the 3 target channels
    collection_result = service.collect_channels(
        run_id=run_id,
        channel_inputs=["@AlphaTech", "@BetaGaming", "@FailingNetwork"],
    )

    # 3. Assert partial execution tolerance
    assert collection_result["status"] == "partial"
    assert len(collection_result["successful_channels"]) == 2
    assert "@AlphaTech" in collection_result["successful_channels"]
    assert "@BetaGaming" in collection_result["successful_channels"]
    assert len(collection_result["failed_channels"]) == 1
    assert collection_result["failed_channels"][0]["channel"] == "@FailingNetwork"
    assert collection_result["failed_channels"][0]["error_code"] == "NetworkError"

    # 4. Verify SQLite database persistence: exactly 35 videos and snapshots
    cursor = test_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM videos")
    assert cursor.fetchone()[0] == 35

    cursor.execute("SELECT COUNT(*) FROM video_snapshots WHERE run_id = ?", (run_id,))
    assert cursor.fetchone()[0] == 35

    cursor.execute(
        "SELECT channel_id, status, videos_collected FROM analysis_run_channels WHERE run_id = ?",
        (run_id,),
    )
    channels_db = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    assert channels_db["UC_ALPHA"] == ("completed", 20)
    assert channels_db["UC_BETA"] == ("completed", 15)
    assert "FailingNetwork" not in channels_db

    # Ensure unresolved channel did not pollute the canonical channels dimension
    cursor.execute("SELECT COUNT(*) FROM channels WHERE channel_id = 'FailingNetwork'")
    assert cursor.fetchone()[0] == 0

    # Verify failed input attempt was tracked in analysis_run_channel_inputs
    cursor.execute(
        "SELECT status, error_code, resolved_channel_id FROM analysis_run_channel_inputs WHERE run_id = ? AND requested_identifier = '@FailingNetwork'",
        (run_id,),
    )
    attempt_row = cursor.fetchone()
    assert attempt_row is not None
    assert (attempt_row[0], attempt_row[1], attempt_row[2]) == ("failed", "NetworkError", None)

    # 5. Calculate and verify statistical metrics
    metrics = service.calculate_run_metrics(run_id)
    assert len(metrics) == 2

    alpha_m = next(m for m in metrics if m.channel_id == "UC_ALPHA")
    assert alpha_m.sample_size_videos == 20
    assert alpha_m.median_views > 0
    assert alpha_m.views_to_subscribers_ratio is not None
    assert alpha_m.format_breakdown.shorts_count == 5
    assert alpha_m.format_breakdown.long_form_count == 15

    beta_m = next(m for m in metrics if m.channel_id == "UC_BETA")
    assert beta_m.sample_size_videos == 15
    assert beta_m.format_breakdown.shorts_count == 3
    assert beta_m.format_breakdown.long_form_count == 12

    # 6. Share of Tracked Channel Views calculation
    tracked_shares = service.get_tracked_channel_views_share(run_id)
    assert len(tracked_shares) == 2
    total_pct = sum(s["share_of_tracked_views_pct"] for s in tracked_shares)
    assert 99.9 <= total_pct <= 100.1  # Shares sum to 100%

    # 7. Generate strategic AI report for Channel A
    report_a = service.generate_channel_report(channel_id="UC_ALPHA", run_id=run_id)
    assert isinstance(report_a, EchoScopeReport)
    assert report_a.channel_handle == "@AlphaTech"
    assert len(report_a.observed_data) == 2
    assert len(report_a.performance_patterns) == 1
    assert len(report_a.strategic_hypotheses) == 1
    assert report_a.strategic_hypotheses[0].confidence_level == "high"

    # 8. Query historical run 100% offline (verifying no API calls made)
    offline_yt_mock = MagicMock(spec=YouTubeClient)
    offline_yt_mock.resolve_channel.side_effect = RuntimeError("Offline assertion violated!")
    offline_yt_mock.fetch_channel_videos_for_period.side_effect = RuntimeError(
        "Offline assertion violated!"
    )

    offline_service = PipelineService(
        conn=test_db,
        youtube_client=offline_yt_mock,
        ai_client=mock_ai,
    )

    overview = offline_service.get_run_overview(run_id)
    assert overview["run"]["run_id"] == run_id
    assert overview["run"]["status"] == "partial"
    assert len(overview["metrics"]) == 2
    assert len(overview["tracked_views_share"]) == 2
    assert not overview["macro_data"].empty

    # Offline videos table retrieval
    vids_df_alpha = offline_service.get_channel_videos(run_id, "UC_ALPHA")
    assert len(vids_df_alpha) == 20
