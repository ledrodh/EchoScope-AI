"""Database schema initialization, legacy data migration, and data retention."""

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

SCHEMA_DDL = """
-- 1. Dimensao de Canais Monitorados
CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    handle TEXT NOT NULL,
    title TEXT NOT NULL,
    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Execucoes de Analise / Coleta (Snapshots temporais)
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id TEXT PRIMARY KEY,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed', 'partial')),
    metrics_version TEXT DEFAULT '1.0'
);

-- 3. Tabela Associativa de Canais por Execucao (Permite execucoes parciais por canal)
CREATE TABLE IF NOT EXISTS analysis_run_channels (
    run_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed', 'empty')),
    videos_collected INTEGER DEFAULT 0,
    error_code TEXT,
    PRIMARY KEY (run_id, channel_id),
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

-- 3b. Tentativas de Coleta por Identificador Solicitado (Evita poluir dimensao channels com IDs nao resolvidos)
CREATE TABLE IF NOT EXISTS analysis_run_channel_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    requested_identifier TEXT NOT NULL,
    resolved_channel_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed', 'empty')),
    error_code TEXT,
    error_message TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, requested_identifier),
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (resolved_channel_id) REFERENCES channels(channel_id) ON DELETE SET NULL
);

-- 4. Medicoes Pontuais do Canal por Execucao
CREATE TABLE IF NOT EXISTS channel_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    subscribers_count INTEGER,
    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, channel_id),
    FOREIGN KEY (run_id, channel_id) REFERENCES analysis_run_channels(run_id, channel_id) ON DELETE CASCADE
);

-- 5. Dimensao de Videos com Restricao Composta de Pertenca ao Canal
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at DATETIME NOT NULL,
    duration_seconds INTEGER NOT NULL,
    UNIQUE (video_id, channel_id),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

-- 6. Medicoes Pontuais de Videos por Execucao com Dupla FK Composta
CREATE TABLE IF NOT EXISTS video_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    views_count INTEGER NOT NULL,
    likes_count INTEGER,
    format_category TEXT NOT NULL CHECK(format_category IN ('Shorts', 'Long-form', 'Unknown')),
    classification_method TEXT DEFAULT 'duration_and_date',
    classification_evidence TEXT DEFAULT '',
    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, video_id),
    FOREIGN KEY (run_id, channel_id) REFERENCES analysis_run_channels(run_id, channel_id) ON DELETE CASCADE,
    FOREIGN KEY (video_id, channel_id) REFERENCES videos(video_id, channel_id) ON DELETE CASCADE
);

-- Indices de Performance
CREATE INDEX IF NOT EXISTS idx_runs_period ON analysis_runs(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_channel_snapshots ON channel_snapshots(channel_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_video_snapshots ON video_snapshots(video_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_videos_channel_date ON videos(channel_id, published_at);
CREATE INDEX IF NOT EXISTS idx_run_inputs ON analysis_run_channel_inputs(run_id, status);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Executes the DDL schema ensuring foreign keys are enabled."""
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_DDL)
    conn.commit()


def backup_database(source_conn: sqlite3.Connection, backup_path: Path) -> Path:
    """Creates a consistent backup copy of an active SQLite database using the native Backup API.

    Prevents corrupt/partial copies under WAL mode by flushing pages cleanly.
    """
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    dest_conn = sqlite3.connect(str(backup_path))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
    return backup_path


def is_legacy_schema(conn: sqlite3.Connection) -> bool:
    """Checks if database contains legacy tables historico_coletas and videos_detalhes."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('historico_coletas', 'videos_detalhes')"
    )
    found_tables = {row[0] for row in cursor.fetchall()}
    return "historico_coletas" in found_tables and "videos_detalhes" in found_tables


def migrate_legacy_db(conn: sqlite3.Connection, db_path: Path | None = None) -> dict[str, int]:
    """Migrates legacy tables into the new relational schema, preserving snapshots."""
    if not is_legacy_schema(conn):
        init_schema(conn)
        return {"migrated_runs": 0, "migrated_videos": 0}

    # Backup using native SQLite backup if physical path is provided
    if db_path is not None and db_path.exists():
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_file = db_path.with_name(f"{db_path.stem}_backup_{timestamp}{db_path.suffix}")
        backup_database(conn, backup_file)

    # Initialize new schema tables
    init_schema(conn)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, channel_handle, inscritos_no_momento, periodo_analisado_inicio, periodo_analisado_fim, data_da_coleta "
        "FROM historico_coletas"
    )
    legacy_coletas = cursor.fetchall()

    migrated_runs = 0
    migrated_videos = 0

    for coleta in legacy_coletas:
        old_id = coleta[0]
        channel_handle = coleta[1]
        subs = coleta[2]
        p_start = coleta[3]
        p_end = coleta[4]
        collected_at = coleta[5]

        # Use handle as fallback channel_id for legacy records
        channel_id = f"legacy_{channel_handle.strip('@')}"
        run_id = f"legacy_run_{old_id}_{uuid.uuid4().hex[:8]}"

        # 1. Upsert channel
        cursor.execute(
            """
            INSERT INTO channels (channel_id, handle, title, first_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET handle=excluded.handle
            """,
            (channel_id, channel_handle, channel_handle, collected_at),
        )

        # 2. Insert run
        cursor.execute(
            """
            INSERT INTO analysis_runs (run_id, period_start, period_end, started_at, finished_at, status, metrics_version)
            VALUES (?, ?, ?, ?, ?, 'completed', '1.0-legacy')
            """,
            (run_id, p_start, p_end, collected_at, collected_at),
        )

        # 3. Associate channel with run
        cursor.execute(
            """
            INSERT INTO analysis_run_channels (run_id, channel_id, status, videos_collected)
            VALUES (?, ?, 'completed', 0)
            """,
            (run_id, channel_id),
        )

        # 4. Insert channel snapshot
        cursor.execute(
            """
            INSERT INTO channel_snapshots (run_id, channel_id, subscribers_count, collected_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, channel_id, subs, collected_at),
        )
        migrated_runs += 1

        # 5. Fetch videos for this old coleta
        cursor.execute(
            """
            SELECT video_id, thumbnail_url, titulo, data_publicacao, categoria, duracao_segundos, views, likes
            FROM videos_detalhes WHERE coleta_id = ?
            """,
            (old_id,),
        )
        videos_detalhes = cursor.fetchall()

        for v in videos_detalhes:
            vid = v[0]
            title = v[2]
            published = v[3]
            cat_legacy = v[4]
            duration = v[5] or 0
            views = v[6] or 0
            likes = v[7]

            format_cat = "Shorts" if cat_legacy == "Shorts" else "Long-form"

            # Upsert video metadata
            cursor.execute(
                """
                INSERT INTO videos (video_id, channel_id, title, published_at, duration_seconds)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO NOTHING
                """,
                (vid, channel_id, title, published, duration),
            )

            # Insert video snapshot
            cursor.execute(
                """
                INSERT INTO video_snapshots (
                    run_id, channel_id, video_id, views_count, likes_count,
                    format_category, classification_method, classification_evidence, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'legacy_migration', 'migrated from videos_detalhes', ?)
                ON CONFLICT(run_id, video_id) DO NOTHING
                """,
                (run_id, channel_id, vid, views, likes, format_cat, collected_at),
            )
            migrated_videos += 1

        # Update run channel video count
        cursor.execute(
            "UPDATE analysis_run_channels SET videos_collected = ? WHERE run_id = ? AND channel_id = ?",
            (len(videos_detalhes), run_id, channel_id),
        )

    conn.commit()
    return {"migrated_runs": migrated_runs, "migrated_videos": migrated_videos}


def purge_expired_records(conn: sqlite3.Connection, retention_days: int = 30) -> dict[str, int]:
    """Removes analysis runs, point-in-time snapshots, and cleans up orphaned metadata dimensions.

    Adheres to the YouTube Data API policy regarding retention of unauthorized third-party metrics
    and stale creator metadata.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Delete expired analysis runs (cascades to snapshots via foreign keys)
    cursor.execute(
        """
        DELETE FROM analysis_runs
        WHERE datetime(started_at) < datetime('now', ?)
        """,
        (f"-{retention_days} days",),
    )
    deleted_runs = cursor.rowcount

    # 2. Clean up orphaned videos whose snapshots are no longer present
    cursor.execute(
        """
        DELETE FROM videos
        WHERE video_id NOT IN (SELECT DISTINCT video_id FROM video_snapshots)
        """
    )
    deleted_videos = cursor.rowcount

    # 3. Clean up orphaned channels with no remaining runs/snapshots
    cursor.execute(
        """
        DELETE FROM channels
        WHERE channel_id NOT IN (SELECT DISTINCT channel_id FROM analysis_run_channels)
        """
    )
    deleted_channels = cursor.rowcount

    conn.commit()
    return {
        "deleted_runs": deleted_runs,
        "deleted_orphaned_videos": deleted_videos,
        "deleted_orphaned_channels": deleted_channels,
    }
