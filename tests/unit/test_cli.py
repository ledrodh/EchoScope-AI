"""Unit tests for EchoScope headless CLI entrypoint."""

import pytest

from echoscope.cli import main


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "EchoScope AI" in captured.out
    assert "collect" in captured.out
    assert "list-runs" in captured.out


def test_cli_list_runs_empty(capsys):
    exit_code = main(["list-runs", "--db", ":memory:"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "No analysis runs recorded yet." in captured.out


def test_cli_invalid_date_format(capsys):
    exit_code = main(
        ["collect", "--channels", "@test", "--start", "invalid-date", "--end", "2026-01-31"]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Invalid date format" in captured.err


def test_cli_invalid_arguments_returns_code_2():
    with pytest.raises(SystemExit) as exc_info:
        main(["collect", "--unknown-argument"])
    assert exc_info.value.code == 2


def test_cli_partial_collection_returns_code_3(monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from echoscope.api.youtube_client import ChannelNotFoundError, ResolvedChannel

    mock_yt = MagicMock()
    mock_yt.quota_units_used = 5

    def _resolve(ch):
        if "invalid" in ch:
            raise ChannelNotFoundError("Not found")
        return ResolvedChannel("UC_1", ch, "Title", "UU_1", 1000)

    mock_yt.resolve_channel.side_effect = _resolve
    mock_yt.fetch_channel_videos_for_period.return_value = ([], [])

    db_path = str(tmp_path / "cli_partial.db")
    monkeypatch.setattr(
        "echoscope.cli.PipelineService",
        lambda db_path=None: PipelineService(db_path=db_path, youtube_client=mock_yt),
    )

    from echoscope.services.pipeline_service import PipelineService

    exit_code = main(
        [
            "collect",
            "--channels",
            "@valid",
            "@invalid",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-31",
            "--db",
            db_path,
        ]
    )
    assert exit_code == 3


def test_cli_overview_not_found(capsys):
    exit_code = main(["overview", "--run-id", "non_existent_run", "--db", ":memory:"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_cli_offline_commands_work_without_api_keys(monkeypatch, capsys, tmp_path):
    # Wipe out any API keys from environment
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    db_path = str(tmp_path / "offline_test.db")

    # list-runs should succeed offline
    code_list = main(["list-runs", "--db", db_path])
    assert code_list == 0

    # overview on missing run should handle error cleanly without crashing on missing API keys
    code_overview = main(["overview", "--run-id", "missing_run", "--db", db_path])
    assert code_overview == 1


def test_cli_overview_and_compare_with_populated_data(capsys, tmp_path):
    from datetime import UTC, date, datetime

    from echoscope.database.models import Video, VideoSnapshot
    from echoscope.services.pipeline_service import PipelineService

    db_path = str(tmp_path / "populated_cli.db")
    service = PipelineService(db_path=db_path)

    # Create run T0
    run_t0 = service.create_analysis_run(
        period_start=date(2026, 1, 1), period_end=date(2026, 1, 15)
    )
    service.conn.execute(
        "INSERT OR IGNORE INTO channels (channel_id, handle, title) VALUES ('UC_A', '@ChannelA', 'Channel A')"
    )
    service.conn.execute(
        "INSERT INTO analysis_run_channels (run_id, channel_id, status, videos_collected) VALUES (?, 'UC_A', 'completed', 1)",
        (run_t0,),
    )
    service.conn.execute(
        "INSERT INTO channel_snapshots (run_id, channel_id, subscribers_count) VALUES (?, 'UC_A', 50000)",
        (run_t0,),
    )
    service.repository.save_video_batch(
        run_t0,
        "UC_A",
        [Video("vid1", "UC_A", "Video 1", datetime(2026, 1, 5, tzinfo=UTC), 300)],
        [
            VideoSnapshot(
                run_id=run_t0,
                channel_id="UC_A",
                video_id="vid1",
                views_count=10000,
                likes_count=500,
                format_category="Long-form",
                classification_method="duration",
                classification_evidence="evidence",
                collected_at=datetime.now(UTC),
            )
        ],
    )
    service.repository.update_run_status(run_t0, "completed")

    # Create run T1
    run_t1 = service.create_analysis_run(
        period_start=date(2026, 1, 1), period_end=date(2026, 1, 25)
    )
    service.conn.execute(
        "INSERT INTO analysis_run_channels (run_id, channel_id, status, videos_collected) VALUES (?, 'UC_A', 'completed', 1)",
        (run_t1,),
    )
    service.conn.execute(
        "INSERT INTO channel_snapshots (run_id, channel_id, subscribers_count) VALUES (?, 'UC_A', 52000)",
        (run_t1,),
    )
    service.repository.save_video_batch(
        run_t1,
        "UC_A",
        [Video("vid1", "UC_A", "Video 1", datetime(2026, 1, 5, tzinfo=UTC), 300)],
        [
            VideoSnapshot(
                run_id=run_t1,
                channel_id="UC_A",
                video_id="vid1",
                views_count=16000,
                likes_count=800,
                format_category="Long-form",
                classification_method="duration",
                classification_evidence="evidence",
                collected_at=datetime.now(UTC),
            )
        ],
    )
    service.repository.update_run_status(run_t1, "completed")
    service.conn.commit()

    # Test CLI overview
    code_ov = main(["overview", "--run-id", run_t0, "--db", db_path])
    assert code_ov == 0
    captured_ov = capsys.readouterr()
    assert "MACRO OVERVIEW" in captured_ov.out
    assert "@ChannelA" in captured_ov.out
    assert "SHARE OF TRACKED CHANNEL VIEWS" in captured_ov.out

    # Test CLI compare
    code_cmp = main(
        ["compare", "--channel-id", "UC_A", "--older", run_t0, "--newer", run_t1, "--db", db_path]
    )
    assert code_cmp == 0
    captured_cmp = capsys.readouterr()
    assert "GROWTH SIGNALS COMPARISON" in captured_cmp.out
    assert "UC_A" in captured_cmp.out
    assert "Gross Positive Growth:     +6,000 views" in captured_cmp.out
