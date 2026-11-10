"""Unit tests for PipelineService orchestrator, headless execution, and architectural boundaries."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from echoscope.ai.gemini_client import create_fallback_report
from echoscope.ai.schemas import EchoScopeReport
from echoscope.analytics.growth import ChannelGrowthComparison
from echoscope.api.youtube_client import (
    ChannelNotFoundError,
    QuotaExceededError,
    ResolvedChannel,
    YouTubeClient,
)
from echoscope.database.connection import configure_sqlite_connection
from echoscope.database.models import Video, VideoSnapshot
from echoscope.services.pipeline_service import PipelineService


@pytest.fixture
def mem_db():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    return configure_sqlite_connection(conn)


@pytest.fixture
def mock_youtube():
    mock = MagicMock(spec=YouTubeClient)
    mock.quota_units_used = 12

    # Mock channel resolution
    def _mock_resolve(ch_input: str):
        if "notfound" in ch_input.lower():
            raise ChannelNotFoundError(f"Channel {ch_input} not found")
        if "quota" in ch_input.lower():
            raise QuotaExceededError("Quota exhausted")
        return ResolvedChannel(
            channel_id=f"UC_{ch_input.replace('@', '')}",
            handle=ch_input,
            title=f"Title of {ch_input}",
            uploads_playlist_id=f"UU_{ch_input.replace('@', '')}",
            subscribers_count=100000,
            is_artist_channel=False,
        )

    mock.resolve_channel.side_effect = _mock_resolve

    # Mock video fetching
    def _mock_fetch(channel, start_date, end_date, run_id, max_pages=25):
        vid = Video(
            video_id=f"vid_{channel.handle}_1",
            channel_id=channel.channel_id,
            title=f"Video 1 by {channel.handle}",
            published_at=datetime(2026, 1, 15, tzinfo=UTC),
            duration_seconds=600,
        )
        snap = VideoSnapshot(
            run_id=run_id,
            channel_id=channel.channel_id,
            video_id=vid.video_id,
            views_count=50000,
            likes_count=2500,
            format_category="Long-form",
            classification_method="duration_and_date",
            classification_evidence="Duration > 180s",
            collected_at=datetime.now(UTC),
        )
        return [vid], [snap]

    mock.fetch_channel_videos_for_period.side_effect = _mock_fetch
    return mock


@pytest.fixture
def mock_ai():
    mock = MagicMock()

    def _mock_gen(channel_metrics, top_videos):
        return create_fallback_report(
            channel_metrics=channel_metrics,
            reason="Mocked AI generation",
            model_name="mock-gemini",
        )

    mock.generate_report.side_effect = _mock_gen
    return mock


def test_create_analysis_run_validation(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)

    # Invalid range: start > end
    with pytest.raises(ValueError, match="Invalid date range"):
        service.create_analysis_run(
            period_start=date(2026, 2, 1),
            period_end=date(2026, 1, 1),
        )

    # Valid run
    run_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    assert run_id.startswith("run_")
    run_info = service.get_run(run_id)
    assert run_info is not None
    assert run_info["status"] == "pending"


def test_collect_channels_success(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )

    result = service.collect_channels(run_id, ["@AlphaChannel", "@BetaChannel"])
    assert result["status"] == "completed"
    assert len(result["successful_channels"]) == 2
    assert len(result["failed_channels"]) == 0

    # Verify metrics calculation
    metrics = service.calculate_run_metrics(run_id)
    assert len(metrics) == 2
    assert metrics[0].sample_size_videos == 1
    assert metrics[0].median_views == 50000.0


def test_collect_channels_partial_failure_tolerance(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )

    result = service.collect_channels(run_id, ["@ValidChannel", "@NotFoundChannel"])
    assert result["status"] == "partial"
    assert len(result["successful_channels"]) == 1
    assert len(result["failed_channels"]) == 1
    assert result["failed_channels"][0]["channel"] == "@NotFoundChannel"

    # Valid channel should be persisted and queryable
    channels = service.get_run_channels(run_id)
    valid_record = next(c for c in channels if c["channel_id"] == "UC_ValidChannel")
    assert valid_record["status"] == "completed"
    assert valid_record["videos_collected"] == 1

    # Unresolved channel MUST NOT pollute the channels dimension with synthetic IDs
    dim_handles = {c["handle"] for c in service.list_channels()}
    assert "@NotFoundChannel" not in dim_handles
    assert "NotFoundChannel" not in {c["channel_id"] for c in service.list_channels()}

    # Input attempts table tracks unresolved failures cleanly
    attempts = service.get_run_input_attempts(run_id)
    failed_att = next(a for a in attempts if a["requested_identifier"] == "@NotFoundChannel")
    assert failed_att["status"] == "failed"
    assert failed_att["error_code"] == "ChannelNotFoundError"
    assert failed_att["resolved_channel_id"] is None


def test_collect_channels_post_resolution_failure_marks_canonical_channel(
    mem_db, mock_youtube, mock_ai
):
    """When resolve_channel succeeds and fetch fails (e.g. QuotaExceeded), marks the real channel_id."""
    from echoscope.api.youtube_client import ResolvedChannel

    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(date(2026, 1, 1), date(2026, 1, 31))

    mock_youtube.resolve_channel.side_effect = None
    mock_youtube.resolve_channel.return_value = ResolvedChannel(
        channel_id="UC_CANONICAL_99",
        handle="@CanonicalChannel",
        title="Canonical Channel",
        uploads_playlist_id="UU_CANONICAL_99",
        subscribers_count=1000,
    )
    mock_youtube.fetch_channel_videos_for_period.side_effect = QuotaExceededError("Quota exceeded!")

    result = service.collect_channels(run_id, ["@CanonicalChannel"])
    assert result["status"] == "failed"

    run_channels = service.get_run_channels(run_id)
    assert len(run_channels) == 1
    assert run_channels[0]["channel_id"] == "UC_CANONICAL_99"
    assert run_channels[0]["status"] == "failed"
    assert run_channels[0]["error_code"] == "QuotaExceededError"


def test_collect_channels_quota_exceeded_handling(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )

    # First channel triggers QuotaExceededError
    result = service.collect_channels(run_id, ["@QuotaChannel", "@SecondChannel"])
    assert result["status"] == "failed"
    assert len(result["failed_channels"]) == 1
    assert result["failed_channels"][0]["error_code"] == "QuotaExceededError"
    # Second channel should not be queried after quota exceeded
    assert "@SecondChannel" not in result["successful_channels"]


def test_query_historical_run_offline(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    service.collect_channels(run_id, ["@TechGuide"])

    # Create an offline service instance with an API client that raises if touched
    failing_yt = MagicMock(spec=YouTubeClient)
    failing_yt.resolve_channel.side_effect = RuntimeError("Offline mode: should not call API!")
    failing_yt.fetch_channel_videos_for_period.side_effect = RuntimeError("Offline!")

    offline_service = PipelineService(conn=mem_db, youtube_client=failing_yt, ai_client=mock_ai)

    overview = offline_service.get_run_overview(run_id)
    assert overview["run"]["run_id"] == run_id
    assert len(overview["metrics"]) == 1
    assert overview["metrics"][0].handle == "@TechGuide"

    # Videos query without API calls
    vids_df = offline_service.get_channel_videos(run_id, "UC_TechGuide")
    assert not vids_df.empty
    assert len(vids_df) == 1


def test_compare_channel_snapshots_across_runs(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)

    # T0 Run
    run_t0 = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 10),
    )
    service.collect_channels(run_t0, ["@GrowthTarget"])

    # T1 Run (10 days later with increased views)
    run_t1 = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 20),
    )

    # Mock T1 video fetch with higher views
    def _mock_fetch_t1(channel, start_date, end_date, run_id, max_pages=25):
        vid = Video(
            video_id=f"vid_{channel.handle}_1",
            channel_id=channel.channel_id,
            title=f"Video 1 by {channel.handle}",
            published_at=datetime(2026, 1, 5, tzinfo=UTC),
            duration_seconds=600,
        )
        snap = VideoSnapshot(
            run_id=run_id,
            channel_id=channel.channel_id,
            video_id=vid.video_id,
            views_count=80000,  # grew from 50k to 80k
            likes_count=3000,
            format_category="Long-form",
            classification_method="duration_and_date",
            classification_evidence="Duration > 180s",
            collected_at=datetime.now(UTC),
        )
        return [vid], [snap]

    mock_youtube.fetch_channel_videos_for_period.side_effect = _mock_fetch_t1
    service.collect_channels(run_t1, ["@GrowthTarget"])

    # Assign explicit snapshot timestamps 10 days apart (even if period_end is identical!)
    mem_db.execute(
        "UPDATE channel_snapshots SET collected_at = '2026-03-01T10:00:00+00:00' WHERE run_id = ?",
        (run_t0,),
    )
    mem_db.execute(
        "UPDATE channel_snapshots SET collected_at = '2026-03-11T10:00:00+00:00' WHERE run_id = ?",
        (run_t1,),
    )
    mem_db.commit()

    growth = service.compare_channel_snapshots(
        channel_id="UC_GrowthTarget",
        run_id_older=run_t0,
        run_id_newer=run_t1,
    )

    assert isinstance(growth, ChannelGrowthComparison)
    assert growth.comparable_videos_count == 1
    assert growth.gross_positive_growth == 30000
    assert growth.days_between_runs == 10.0
    assert growth.daily_growth_velocity == 3000.0


def test_growth_signals_with_identical_period_end_uses_snapshot_timestamps(
    mem_db, mock_youtube, mock_ai
):
    """Two runs covering the exact same period_end use real channel_snapshots.collected_at."""
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)

    run_t0 = service.create_analysis_run(date(2026, 1, 1), date(2026, 2, 28))
    service.collect_channels(run_t0, ["@SameWindow"])

    run_t1 = service.create_analysis_run(date(2026, 1, 1), date(2026, 2, 28))
    service.collect_channels(run_t1, ["@SameWindow"])

    # Simulate 10 real days between collections despite identical period_end
    mem_db.execute(
        "UPDATE channel_snapshots SET collected_at = '2026-03-01T00:00:00+00:00' WHERE run_id = ?",
        (run_t0,),
    )
    mem_db.execute(
        "UPDATE channel_snapshots SET collected_at = '2026-03-11T00:00:00+00:00' WHERE run_id = ?",
        (run_t1,),
    )
    mem_db.execute(
        "UPDATE video_snapshots SET views_count = 70000 WHERE run_id = ? AND channel_id = 'UC_SameWindow'",
        (run_t1,),
    )
    mem_db.commit()

    growth = service.compare_channel_snapshots("UC_SameWindow", run_t0, run_t1)
    assert growth.days_between_runs == 10.0
    assert growth.gross_positive_growth == 20000
    assert growth.daily_growth_velocity == 2000.0


def test_vpd_uses_channel_specific_collected_at(mem_db, mock_youtube, mock_ai):
    """Ensures VPD is anchored to each channel's own channel_snapshots.collected_at."""
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(date(2026, 1, 1), date(2026, 1, 31))
    service.collect_channels(run_id, ["@ChannelFast", "@ChannelLate"])

    # Video published on 2026-01-15 with 50,000 views
    # Fast collected on 2026-01-20 (5 days elapsed -> 10,000 VPD)
    # Late collected on 2026-01-25 (10 days elapsed -> 5,000 VPD)
    mem_db.execute(
        "UPDATE channel_snapshots SET collected_at = '2026-01-20T00:00:00+00:00' WHERE run_id = ? AND channel_id = 'UC_ChannelFast'",
        (run_id,),
    )
    mem_db.execute(
        "UPDATE channel_snapshots SET collected_at = '2026-01-25T00:00:00+00:00' WHERE run_id = ? AND channel_id = 'UC_ChannelLate'",
        (run_id,),
    )
    mem_db.commit()

    metrics_by_id = {m.channel_id: m for m in service.calculate_run_metrics(run_id)}
    assert metrics_by_id["UC_ChannelFast"].median_views_per_day == 10000.0
    assert metrics_by_id["UC_ChannelLate"].median_views_per_day == 5000.0


def test_generate_channel_report(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    service.collect_channels(run_id, ["@ReportChannel"])

    report = service.generate_channel_report("UC_ReportChannel", run_id)
    assert isinstance(report, EchoScopeReport)
    assert len(report.observed_data) >= 2
    assert report.model_used == "mock-gemini"


def test_reconcile_interrupted_runs(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)

    # 1. Simulate an interrupted run that collected 1 channel before crashing
    run_partial_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    service.collect_channels(run_partial_id, ["@AlphaChannel"])
    # Artificially set status back to 'running' to simulate an ungraceful crash midway
    service.conn.execute(
        "UPDATE analysis_runs SET status = 'running', finished_at = NULL WHERE run_id = ?",
        (run_partial_id,),
    )

    # 2. Simulate an interrupted run that crashed before collecting any videos
    run_empty_id = service.create_analysis_run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    service.conn.execute(
        "UPDATE analysis_runs SET status = 'running', finished_at = NULL WHERE run_id = ?",
        (run_empty_id,),
    )
    service.conn.commit()

    # Reconcile abandoned runs
    reconciled_count = service.reconcile_interrupted_runs()
    assert reconciled_count == 2

    # Run with videos should be marked 'partial'
    run_partial_info = service.get_run(run_partial_id)
    assert run_partial_info is not None
    assert run_partial_info["status"] == "partial"
    assert run_partial_info["finished_at"] is not None

    # Run with zero videos should be marked 'failed'
    run_empty_info = service.get_run(run_empty_id)
    assert run_empty_info is not None
    assert run_empty_info["status"] == "failed"
    assert run_empty_info["finished_at"] is not None


def test_parse_date_helper_branches():
    from datetime import UTC, date, datetime

    from echoscope.services.pipeline_service import _parse_date

    # date input
    d = date(2026, 1, 15)
    assert _parse_date(d) == d

    # datetime input
    dt = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert _parse_date(dt) == d

    # string input
    assert _parse_date("2026-01-15T00:00:00") == d

    # Invalid input
    with pytest.raises(ValueError, match="Cannot parse date"):
        _parse_date(12345)


def test_collect_channels_empty_strings_and_empty_videos_branch(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_id = service.create_analysis_run(date(2026, 1, 1), date(2026, 1, 31))

    # Mock fetch returns 0 videos for this channel
    mock_youtube.fetch_channel_videos_for_period.side_effect = None
    mock_youtube.fetch_channel_videos_for_period.return_value = ([], [])

    result = service.collect_channels(run_id, ["   ", "@EmptyChannel", ""])
    assert result["status"] == "completed"
    assert len(result["successful_channels"]) == 1

    # Status of channel should be 'empty'
    channels = service.get_run_channels(run_id)
    assert channels[0]["status"] == "empty"


def test_compare_channel_snapshots_missing_runs_raise_value_error(mem_db, mock_youtube, mock_ai):
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    run_valid = service.create_analysis_run(date(2026, 1, 1), date(2026, 1, 31))

    with pytest.raises(ValueError, match="Older run 'non_existent_older' not found"):
        service.compare_channel_snapshots("UC_X", "non_existent_older", run_valid)

    with pytest.raises(ValueError, match="Newer run 'non_existent_newer' not found"):
        service.compare_channel_snapshots("UC_X", run_valid, "non_existent_newer")


def test_pipeline_service_list_channels(mem_db, mock_youtube, mock_ai):
    """Verifies that PipelineService lists registered channels from dimension."""
    from echoscope.database.repository import Channel

    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    assert service.list_channels() == []

    service.repository.upsert_channel(
        Channel(channel_id="UC_1", handle="@TechLead", title="Tech Lead")
    )
    chans = service.list_channels()
    assert len(chans) == 1
    assert chans[0]["handle"] == "@TechLead"


def test_pipeline_service_seed_demo_dataset_and_api_configured_properties(
    mem_db, mock_youtube, mock_ai
):
    """Validates that seed_demo_dataset creates two historical runs and populates channels."""
    mock_youtube.api_key = "mock_key"
    mock_ai.api_key = "mock_key"
    service = PipelineService(conn=mem_db, youtube_client=mock_youtube, ai_client=mock_ai)
    assert service.is_youtube_configured is True
    assert service.is_gemini_configured is True

    newer_run_id = service.seed_demo_dataset()
    assert newer_run_id is not None
    runs = service.list_runs()
    assert len(runs) == 2

    overview = service.get_run_overview(newer_run_id)
    assert len(overview["metrics"]) == 3
    assert len(overview["macro_data"]) == 3


def test_architecture_boundaries_ast_inspection():
    """Validates via Python AST that UI layer never imports database or API infrastructure directly."""
    import ast
    from pathlib import Path

    ui_dir = Path(__file__).parent.parent.parent / "ui"
    forbidden_modules = {
        "sqlite3",
        "googleapiclient",
        "google.genai",
        "google.generativeai",
        "echoscope.database.repository",
        "echoscope.api.youtube_client",
        "echoscope.ai.gemini_client",
    }

    py_files = list(ui_dir.glob("**/*.py"))
    assert len(py_files) > 0, "UI directory contains no python files to test"

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_modules:
                        assert not alias.name.startswith(
                            forbidden
                        ), f"Forbidden import '{alias.name}' detected in UI file {py_file.name}:{node.lineno}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for forbidden in forbidden_modules:
                        assert not node.module.startswith(
                            forbidden
                        ), f"Forbidden from-import '{node.module}' detected in UI file {py_file.name}:{node.lineno}"
