"""Parameterized SQL repository for EchoScope AI."""

import sqlite3
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from echoscope.database.models import (
    AnalysisRun,
    Channel,
    ChannelRunStatus,
    ChannelSnapshot,
    RunStatus,
    Video,
    VideoSnapshot,
)


def _to_iso(dt: datetime | date | str | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime | date):
        return dt.isoformat()
    return str(dt)


class EchoScopeRepository:
    """Manages transactional persistence and data queries using strictly parameterized SQL."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA busy_timeout = 30000;")

    def upsert_channel(self, channel: Channel) -> None:
        """Inserts or updates channel dimension record."""
        self.conn.execute(
            """
            INSERT INTO channels (channel_id, handle, title, first_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                handle = excluded.handle,
                title = excluded.title
            """,
            (channel.channel_id, channel.handle, channel.title, _to_iso(channel.first_seen_at)),
        )
        self.conn.commit()

    def create_run(self, run: AnalysisRun) -> str:
        """Registers a new distinct analysis execution."""
        self.conn.execute(
            """
            INSERT INTO analysis_runs (run_id, period_start, period_end, started_at, finished_at, status, metrics_version)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                _to_iso(run.period_start),
                _to_iso(run.period_end),
                _to_iso(run.started_at),
                _to_iso(run.finished_at),
                run.status,
                run.metrics_version,
            ),
        )
        self.conn.commit()
        return run.run_id

    def associate_channel_run(
        self,
        run_id: str,
        channel: Channel,
        status: ChannelRunStatus = "pending",
        error_code: str | None = None,
    ) -> None:
        """Ensures channel dimension exists and registers it under the specific execution."""
        self.upsert_channel(channel)
        self.conn.execute(
            """
            INSERT INTO analysis_run_channels (run_id, channel_id, status, videos_collected, error_code)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(run_id, channel_id) DO UPDATE SET
                status = excluded.status,
                error_code = excluded.error_code
            """,
            (run_id, channel.channel_id, status, error_code),
        )
        self.conn.commit()

    def mark_channel_failed(
        self,
        run_id: str,
        channel_id: str,
        error_code: str,
    ) -> None:
        """Marks an already-resolved channel collection as failed within an execution run."""
        self.conn.execute(
            """
            INSERT INTO analysis_run_channels (run_id, channel_id, status, videos_collected, error_code)
            VALUES (?, ?, 'failed', 0, ?)
            ON CONFLICT(run_id, channel_id) DO UPDATE SET
                status = 'failed',
                error_code = excluded.error_code
            """,
            (run_id, channel_id, error_code),
        )
        self.conn.commit()

    def record_channel_input_attempt(
        self,
        run_id: str,
        requested_identifier: str,
        resolved_channel_id: str | None,
        status: ChannelRunStatus,
        error_code: str | None = None,
        error_message: str = "",
    ) -> None:
        """Records user-requested identifier resolution/collection outcome without creating fake channels."""
        self.conn.execute(
            """
            INSERT INTO analysis_run_channel_inputs (
                run_id, requested_identifier, resolved_channel_id, status, error_code, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, requested_identifier) DO UPDATE SET
                resolved_channel_id = COALESCE(excluded.resolved_channel_id, analysis_run_channel_inputs.resolved_channel_id),
                status = excluded.status,
                error_code = excluded.error_code,
                error_message = excluded.error_message
            """,
            (
                run_id,
                requested_identifier,
                resolved_channel_id,
                status,
                error_code,
                error_message,
            ),
        )
        self.conn.commit()

    def get_run_input_attempts(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieves all requested channel input attempts for a given run."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                id, run_id, requested_identifier, resolved_channel_id,
                status, error_code, error_message, created_at
            FROM analysis_run_channel_inputs
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def record_channel_snapshot(self, snapshot: ChannelSnapshot) -> None:
        """Records point-in-time subscriber snapshot for a channel in a specific run."""
        self.conn.execute(
            """
            INSERT INTO channel_snapshots (run_id, channel_id, subscribers_count, collected_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, channel_id) DO UPDATE SET
                subscribers_count = excluded.subscribers_count,
                collected_at = excluded.collected_at
            """,
            (
                snapshot.run_id,
                snapshot.channel_id,
                snapshot.subscribers_count,
                _to_iso(snapshot.collected_at),
            ),
        )
        self.conn.commit()

    def save_video_batch(
        self,
        run_id: str,
        channel_id: str,
        videos: list[Video],
        snapshots: list[VideoSnapshot],
    ) -> None:
        """Persists videos dimension and snapshot facts in a single operation."""
        # 1. Upsert videos dimension
        self.conn.executemany(
            """
            INSERT INTO videos (video_id, channel_id, title, published_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                duration_seconds = excluded.duration_seconds
            """,
            [
                (v.video_id, v.channel_id, v.title, _to_iso(v.published_at), v.duration_seconds)
                for v in videos
            ],
        )

        # 2. Insert video snapshots
        self.conn.executemany(
            """
            INSERT INTO video_snapshots (
                run_id, channel_id, video_id, views_count, likes_count,
                format_category, classification_method, classification_evidence, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, video_id) DO UPDATE SET
                views_count = excluded.views_count,
                likes_count = excluded.likes_count,
                format_category = excluded.format_category,
                collected_at = excluded.collected_at
            """,
            [
                (
                    s.run_id,
                    s.channel_id,
                    s.video_id,
                    s.views_count,
                    s.likes_count,
                    s.format_category,
                    s.classification_method,
                    s.classification_evidence,
                    _to_iso(s.collected_at),
                )
                for s in snapshots
            ],
        )

        # 3. Update count in associative table
        self.conn.execute(
            """
            UPDATE analysis_run_channels
            SET videos_collected = ?, status = 'completed'
            WHERE run_id = ? AND channel_id = ?
            """,
            (len(snapshots), run_id, channel_id),
        )
        self.conn.commit()

    def update_run_status(self, run_id: str, status: RunStatus) -> None:
        """Updates run state and records completion timestamp when terminal."""
        finished_at = datetime.now(UTC) if status in ("completed", "failed", "partial") else None
        self.conn.execute(
            """
            UPDATE analysis_runs
            SET status = ?, finished_at = COALESCE(?, finished_at)
            WHERE run_id = ?
            """,
            (status, _to_iso(finished_at), run_id),
        )
        self.conn.commit()

    def reconcile_interrupted_runs(self) -> int:
        """Finds any abandoned 'running' executions and transitions them to 'partial' or 'failed'.

        Preserves all snapshots and channel dimensions already committed.
        Returns the number of reconciled runs.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT run_id FROM analysis_runs WHERE status = 'running'")
        running_runs = [row[0] for row in cursor.fetchall()]

        if not running_runs:
            return 0

        now_iso = _to_iso(datetime.now(UTC))
        for run_id in running_runs:
            cursor.execute(
                """
                SELECT COALESCE(SUM(videos_collected), 0)
                FROM analysis_run_channels
                WHERE run_id = ?
                """,
                (run_id,),
            )
            vids_sum = cursor.fetchone()[0]
            new_status: RunStatus = "partial" if vids_sum > 0 else "failed"

            self.conn.execute(
                """
                UPDATE analysis_run_channels
                SET status = 'failed', error_code = 'InterruptedRun'
                WHERE run_id = ? AND status = 'pending'
                """,
                (run_id,),
            )

            self.conn.execute(
                """
                UPDATE analysis_runs
                SET status = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (new_status, now_iso, run_id),
            )

        self.conn.commit()
        return len(running_runs)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Retrieves a single run by run_id."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT run_id, period_start, period_end, started_at, finished_at, status, metrics_version
            FROM analysis_runs
            WHERE run_id = ?
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_channel(self, channel_id: str) -> Channel | None:
        """Retrieves a channel by ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT channel_id, handle, title, first_seen_at
            FROM channels
            WHERE channel_id = ?
            """,
            (channel_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, sqlite3.Row) or isinstance(row, dict):
            return Channel(
                channel_id=row["channel_id"],
                handle=row["handle"],
                title=row["title"],
                first_seen_at=row["first_seen_at"],
            )
        return Channel(
            channel_id=row[0],
            handle=row[1],
            title=row[2],
            first_seen_at=row[3],
        )

    def list_channels(self) -> list[Channel]:
        """Retrieves all registered channels."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT channel_id, handle, title, first_seen_at
            FROM channels
            ORDER BY title ASC
            """
        )
        channels: list[Channel] = []
        for row in cursor.fetchall():
            if isinstance(row, sqlite3.Row) or isinstance(row, dict):
                channels.append(
                    Channel(
                        channel_id=row["channel_id"],
                        handle=row["handle"],
                        title=row["title"],
                        first_seen_at=row["first_seen_at"],
                    )
                )
            else:
                channels.append(
                    Channel(
                        channel_id=row[0],
                        handle=row[1],
                        title=row[2],
                        first_seen_at=row[3],
                    )
                )
        return channels

    def get_channel_snapshot(self, run_id: str, channel_id: str) -> dict[str, Any] | None:
        """Retrieves channel snapshot metadata (subscribers_count, collected_at) for a run."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT run_id, channel_id, subscribers_count, collected_at
            FROM channel_snapshots
            WHERE run_id = ? AND channel_id = ?
            """,
            (run_id, channel_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_run_channels(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieves all channels participating in an analysis run."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                arc.channel_id,
                c.handle,
                c.title,
                arc.status,
                arc.videos_collected,
                arc.error_code,
                cs.subscribers_count,
                cs.collected_at
            FROM analysis_run_channels arc
            JOIN channels c ON arc.channel_id = c.channel_id
            LEFT JOIN channel_snapshots cs ON arc.run_id = cs.run_id AND arc.channel_id = cs.channel_id
            WHERE arc.run_id = ?
            ORDER BY c.title ASC
            """,
            (run_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_runs(self) -> list[dict[str, Any]]:
        """Returns completed runs metadata ordered by recency."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                r.run_id,
                r.period_start,
                r.period_end,
                r.started_at,
                r.status,
                COUNT(DISTINCT arc.channel_id) as total_channels,
                COALESCE(SUM(arc.videos_collected), 0) as total_videos
            FROM analysis_runs r
            LEFT JOIN analysis_run_channels arc ON r.run_id = arc.run_id
            GROUP BY r.run_id
            ORDER BY r.started_at DESC
            """
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_run_macro_data(self, run_id: str) -> pd.DataFrame:
        """Retrieves aggregated metrics strictly scoped to ONE execution run_id.

        Guarantees that multiple collections of the same date range never produce duplicate sums.
        """
        query = """
            SELECT
                c.channel_id,
                c.handle as Channel,
                cs.subscribers_count as Subs,
                COUNT(vs.id) as Videos_Published,
                COALESCE(SUM(vs.views_count), 0) as Total_Views,
                COALESCE(SUM(CASE WHEN vs.format_category = 'Long-form' THEN vs.views_count ELSE 0 END), 0) as Long_Views,
                COALESCE(SUM(CASE WHEN vs.format_category = 'Shorts' THEN vs.views_count ELSE 0 END), 0) as Shorts_Views,
                COALESCE(SUM(CASE WHEN vs.format_category = 'Unknown' THEN vs.views_count ELSE 0 END), 0) as Unknown_Views,
                COALESCE(SUM(vs.likes_count), 0) as Total_Likes
            FROM analysis_run_channels arc
            JOIN channels c ON arc.channel_id = c.channel_id
            LEFT JOIN channel_snapshots cs ON arc.run_id = cs.run_id AND arc.channel_id = cs.channel_id
            LEFT JOIN video_snapshots vs ON arc.run_id = vs.run_id AND arc.channel_id = vs.channel_id
            WHERE arc.run_id = ? AND arc.status = 'completed'
            GROUP BY c.channel_id, c.handle, cs.subscribers_count
            ORDER BY Total_Views DESC
        """
        return pd.read_sql_query(query, self.conn, params=[run_id])

    def get_run_channel_videos(self, run_id: str, channel_id: str) -> pd.DataFrame:
        """Retrieves individual video metrics for a specific channel within a specific run."""
        query = """
            SELECT
                v.video_id,
                v.title,
                v.published_at as date,
                v.duration_seconds,
                vs.views_count as views,
                vs.likes_count as likes,
                vs.format_category as category,
                vs.classification_method,
                vs.classification_evidence
            FROM video_snapshots vs
            JOIN videos v ON vs.video_id = v.video_id
            WHERE vs.run_id = ? AND vs.channel_id = ?
            ORDER BY vs.views_count DESC
        """
        return pd.read_sql_query(query, self.conn, params=[run_id, channel_id])
