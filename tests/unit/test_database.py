"""Unit tests for EchoScope relational database and snapshots (Phase 2)."""

import sqlite3
from datetime import UTC, date, datetime

import pytest

from echoscope.database.connection import configure_sqlite_connection
from echoscope.database.migrations import (
    backup_database,
    init_schema,
    is_legacy_schema,
    migrate_legacy_db,
    purge_expired_records,
)
from echoscope.database.models import (
    AnalysisRun,
    Channel,
    ChannelSnapshot,
    Video,
    VideoSnapshot,
)
from echoscope.database.repository import EchoScopeRepository


@pytest.fixture
def memory_db():
    """Provides an in-memory SQLite database with foreign keys and schema initialized."""
    conn = sqlite3.connect(":memory:")
    configure_sqlite_connection(conn)
    init_schema(conn)
    yield conn
    conn.close()


def test_schema_fk_enforcement(memory_db):
    """Ensures foreign keys are active and block orphaned records."""
    cursor = memory_db.cursor()

    # Attempt inserting an associator record for a nonexistent run
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO analysis_run_channels (run_id, channel_id, status) VALUES ('run_999', 'chan_999', 'pending')"
        )


def test_channel_snapshot_requires_run_channel_parent(memory_db):
    """Guarantees channel_snapshots must point to a valid (run_id, channel_id) in analysis_run_channels."""
    repo = EchoScopeRepository(memory_db)
    run = AnalysisRun(run_id="run_1", period_start=date(2026, 1, 1), period_end=date(2026, 3, 1))
    repo.create_run(run)

    # Attempting to record snapshot without associating channel to run must fail
    with pytest.raises(sqlite3.IntegrityError):
        repo.record_channel_snapshot(
            ChannelSnapshot(run_id="run_1", channel_id="unlinked_channel", subscribers_count=500)
        )


def test_video_snapshot_blocks_mismatched_channel(memory_db):
    """CRITICAL INTEGRITY TEST: Guarantees video_snapshot rejects video_id if associated with a different channel."""
    repo = EchoScopeRepository(memory_db)
    run = AnalysisRun(
        run_id="run_integrity", period_start=date(2026, 1, 1), period_end=date(2026, 2, 1)
    )
    repo.create_run(run)

    chan_a = Channel(channel_id="UC_CHAN_A", handle="@ChannelA", title="Channel A")
    chan_b = Channel(channel_id="UC_CHAN_B", handle="@ChannelB", title="Channel B")
    repo.associate_channel_run(run.run_id, chan_a, status="completed")
    repo.associate_channel_run(run.run_id, chan_b, status="completed")

    # Video belongs legitimately to Channel A
    vid_a = Video(
        video_id="vid_owned_by_a",
        channel_id=chan_a.channel_id,
        title="Video A",
        published_at=datetime(2026, 1, 15, tzinfo=UTC),
        duration_seconds=300,
    )
    repo.upsert_channel(chan_a)
    memory_db.execute(
        "INSERT INTO videos (video_id, channel_id, title, published_at, duration_seconds) VALUES (?, ?, ?, ?, ?)",
        (
            vid_a.video_id,
            vid_a.channel_id,
            vid_a.title,
            vid_a.published_at.isoformat(),
            vid_a.duration_seconds,
        ),
    )

    # Attempting to record snapshot asserting this video belongs to Channel B must violate FK (video_id, channel_id)
    with pytest.raises(sqlite3.IntegrityError):
        memory_db.execute(
            """
            INSERT INTO video_snapshots (
                run_id, channel_id, video_id, views_count, format_category
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run.run_id, chan_b.channel_id, vid_a.video_id, 5000, "Long-form"),
        )


def test_multi_snapshot_isolation(memory_db):
    """CRITICAL REGRESSION TEST: Ensures two collections of the same channel and date range
    never sum views together, maintaining isolated temporal snapshots.
    """
    repo = EchoScopeRepository(memory_db)
    channel = Channel(channel_id="UC_TECH_01", handle="@TechDaily", title="Tech Daily")

    # Run 1: Collected on March 1st (Jan-Feb period) -> 100,000 views
    run1 = AnalysisRun(
        run_id="run_march_01", period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
    )
    repo.create_run(run1)
    repo.associate_channel_run(run1.run_id, channel, status="completed")
    repo.record_channel_snapshot(
        ChannelSnapshot(run_id=run1.run_id, channel_id=channel.channel_id, subscribers_count=50000)
    )

    video1 = Video(
        video_id="vid_101",
        channel_id=channel.channel_id,
        title="Review 1",
        published_at=datetime(2026, 1, 15, tzinfo=UTC),
        duration_seconds=600,
    )
    snap1 = VideoSnapshot(
        run_id=run1.run_id,
        channel_id=channel.channel_id,
        video_id=video1.video_id,
        views_count=100000,
        format_category="Long-form",
    )
    repo.save_video_batch(run1.run_id, channel.channel_id, [video1], [snap1])

    # Run 2: Collected on April 1st (Same Jan-Feb period, re-measured) -> 120,000 views
    run2 = AnalysisRun(
        run_id="run_april_01", period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
    )
    repo.create_run(run2)
    repo.associate_channel_run(run2.run_id, channel, status="completed")
    repo.record_channel_snapshot(
        ChannelSnapshot(run_id=run2.run_id, channel_id=channel.channel_id, subscribers_count=52000)
    )

    snap2 = VideoSnapshot(
        run_id=run2.run_id,
        channel_id=channel.channel_id,
        video_id=video1.video_id,
        views_count=120000,
        format_category="Long-form",
    )
    repo.save_video_batch(run2.run_id, channel.channel_id, [video1], [snap2])

    # Query Macro Data for Run 1
    df1 = repo.get_run_macro_data(run1.run_id)
    assert len(df1) == 1
    assert df1.iloc[0]["Total_Views"] == 100000, "Run 1 must show exactly 100,000 views!"

    # Query Macro Data for Run 2
    df2 = repo.get_run_macro_data(run2.run_id)
    assert len(df2) == 1
    assert df2.iloc[0]["Total_Views"] == 120000, "Run 2 must show exactly 120,000 views!"


def test_partial_run_support(memory_db):
    """Checks that runs tolerate failed or empty channels without losing data from successful ones."""
    repo = EchoScopeRepository(memory_db)
    run = AnalysisRun(
        run_id="run_partial", period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
    )
    repo.create_run(run)

    chan1 = Channel(channel_id="UC_01", handle="@ValidChan", title="Valid Channel")
    chan2 = Channel(channel_id="UC_02", handle="@FailedChan", title="Failed Channel")

    repo.associate_channel_run(run.run_id, chan1, status="completed")
    repo.record_channel_snapshot(
        ChannelSnapshot(run_id=run.run_id, channel_id=chan1.channel_id, subscribers_count=10000)
    )
    vid = Video(
        video_id="v_01",
        channel_id=chan1.channel_id,
        title="Ep 1",
        published_at=datetime(2026, 1, 10, tzinfo=UTC),
        duration_seconds=120,
    )
    snap = VideoSnapshot(
        run_id=run.run_id,
        channel_id=chan1.channel_id,
        video_id=vid.video_id,
        views_count=5000,
        format_category="Long-form",
    )
    repo.save_video_batch(run.run_id, chan1.channel_id, [vid], [snap])

    repo.associate_channel_run(run.run_id, chan2, status="failed")
    repo.update_run_status(run.run_id, status="partial")

    macro = repo.get_run_macro_data(run.run_id)
    assert len(macro) == 1
    assert macro.iloc[0]["Channel"] == "@ValidChan"
    assert macro.iloc[0]["Total_Views"] == 5000


def test_legacy_migration_with_validation():
    """Simulates a legacy SQLite database with historico_coletas and validates migration."""
    conn = sqlite3.connect(":memory:")
    configure_sqlite_connection(conn)

    # Build legacy tables
    conn.execute(
        """
        CREATE TABLE historico_coletas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_handle TEXT,
            inscritos_no_momento INTEGER,
            periodo_analisado_inicio DATE,
            periodo_analisado_fim DATE,
            data_da_coleta DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE videos_detalhes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coleta_id INTEGER,
            video_id TEXT,
            thumbnail_url TEXT,
            titulo TEXT,
            data_publicacao DATE,
            categoria TEXT,
            duracao_segundos INTEGER,
            views INTEGER,
            likes INTEGER,
            eficiencia_percent REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO historico_coletas VALUES (1, '@OldChannel', 10000, '2026-01-01', '2026-02-01', '2026-02-02 12:00:00')"
    )
    conn.execute(
        "INSERT INTO videos_detalhes VALUES (10, 1, 'vid_legacy_1', 'thumb.jpg', 'Title 1', '2026-01-15', 'Shorts', 50, 2000, 100, 20.0)"
    )
    conn.commit()

    assert is_legacy_schema(conn)

    # Run migration
    results = migrate_legacy_db(conn)
    assert results["migrated_runs"] == 1
    assert results["migrated_videos"] == 1

    repo = EchoScopeRepository(conn)
    runs = repo.list_runs()
    assert len(runs) == 1
    assert runs[0]["total_videos"] == 1

    macro = repo.get_run_macro_data(runs[0]["run_id"])
    assert len(macro) == 1
    assert macro.iloc[0]["Shorts_Views"] == 2000

    conn.close()


def test_retention_policy_purge_and_orphans_cleanup(memory_db):
    """Verifies that purge_expired_records cascades to snapshots and purges orphaned dimensions."""
    cursor = memory_db.cursor()

    # Insert channel and video
    cursor.execute(
        "INSERT INTO channels (channel_id, handle, title) VALUES ('chan_old', '@Old', 'Old Channel')"
    )
    cursor.execute(
        "INSERT INTO videos (video_id, channel_id, title, published_at, duration_seconds) VALUES ('vid_old', 'chan_old', 'Old Video', '2025-01-15', 60)"
    )

    # Manually insert run from 60 days ago
    cursor.execute(
        """
        INSERT INTO analysis_runs (run_id, period_start, period_end, started_at, status)
        VALUES ('run_old_60d', '2025-01-01', '2025-02-01', datetime('now', '-60 days'), 'completed')
        """
    )
    cursor.execute(
        "INSERT INTO analysis_run_channels (run_id, channel_id, status) VALUES ('run_old_60d', 'chan_old', 'completed')"
    )
    cursor.execute(
        "INSERT INTO video_snapshots (run_id, channel_id, video_id, views_count, format_category) VALUES ('run_old_60d', 'chan_old', 'vid_old', 1000, 'Long-form')"
    )

    memory_db.commit()

    # Purge runs older than 30 days
    report = purge_expired_records(memory_db, retention_days=30)
    assert report["deleted_runs"] == 1
    assert report["deleted_orphaned_videos"] == 1
    assert report["deleted_orphaned_channels"] == 1

    # Verify tables are completely clean of orphaned dimensions
    cursor.execute("SELECT COUNT(*) FROM analysis_runs")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM videos")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM channels")
    assert cursor.fetchone()[0] == 0


def test_sqlite_wal_backup_and_restore(tmp_path):
    """Verifies that backup_database uses the SQLite backup API consistently under WAL mode."""
    db_file = tmp_path / "primary.db"
    conn = sqlite3.connect(str(db_file))
    configure_sqlite_connection(conn)
    init_schema(conn)

    repo = EchoScopeRepository(conn)
    chan = Channel(channel_id="UC_WAL", handle="@WALChan", title="WAL Channel")
    repo.upsert_channel(chan)
    conn.commit()

    # Perform online backup
    backup_file = tmp_path / "backup.db"
    backup_database(conn, backup_file)
    conn.close()

    assert backup_file.exists()

    # Open backup file and verify data integrity
    restore_conn = sqlite3.connect(str(backup_file))
    configure_sqlite_connection(restore_conn)
    cursor = restore_conn.cursor()
    cursor.execute("SELECT handle FROM channels WHERE channel_id = 'UC_WAL'")
    row = cursor.fetchone()
    assert row is not None
    assert row["handle"] == "@WALChan"
    restore_conn.close()


def test_transaction_context_manager_commit_and_rollback(tmp_path):
    """Validates atomic transaction context manager commit on success and rollback on exception."""
    from echoscope.database.connection import transaction

    db_path = tmp_path / "trans_test.db"

    # 1. Success path (commit)
    with transaction(db_path) as conn:
        init_schema(conn)
        conn.execute(
            "INSERT INTO channels (channel_id, handle, title) VALUES ('UC_T1', '@T1', 'Title 1')"
        )

    # Verify committed
    with transaction(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM channels WHERE channel_id = 'UC_T1'")
        assert cursor.fetchone()[0] == 1

    # 2. Failure path (rollback)
    with pytest.raises(RuntimeError, match="Rollback triggered"):
        with transaction(db_path) as conn:
            conn.execute(
                "INSERT INTO channels (channel_id, handle, title) VALUES ('UC_FAIL', '@Fail', 'Fail')"
            )
            raise RuntimeError("Rollback triggered")

    # Verify rolled back
    with transaction(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM channels WHERE channel_id = 'UC_FAIL'")
        assert cursor.fetchone()[0] == 0


def test_database_helpers_and_non_legacy_migration(tmp_path):
    """Validates repository helper methods and early-exit on non-legacy databases."""
    from datetime import UTC, date, datetime

    from echoscope.database.repository import _to_iso

    # _to_iso helper
    assert _to_iso(None) is None
    assert _to_iso("2026-01-01") == "2026-01-01"
    assert _to_iso(date(2026, 1, 1)) == "2026-01-01"
    assert _to_iso(datetime(2026, 1, 1, 12, 0, tzinfo=UTC)) == "2026-01-01T12:00:00+00:00"

    # Non-legacy migration returns early
    clean_conn = sqlite3.connect(":memory:")
    init_schema(clean_conn)
    result = migrate_legacy_db(clean_conn)
    assert result == {"migrated_runs": 0, "migrated_videos": 0}

    # get_channel returns None when absent
    repo = EchoScopeRepository(clean_conn)
    assert repo.get_channel("NON_EXISTENT") is None
    assert repo.list_channels() == []

    repo.upsert_channel(Channel(channel_id="UC_1", handle="@chan1", title="Alpha"))
    repo.upsert_channel(Channel(channel_id="UC_2", handle="@chan2", title="Beta"))
    channels = repo.list_channels()
    assert len(channels) == 2
    assert [c.handle for c in channels] == ["@chan1", "@chan2"]
