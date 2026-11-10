"""Application pipeline orchestrator decoupling database, YouTube API, metrics, and UI."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from echoscope.ai.gemini_client import GeminiAnalysisClient
from echoscope.ai.schemas import EchoScopeReport
from echoscope.analytics.growth import ChannelGrowthComparison, compute_channel_growth
from echoscope.analytics.metrics import (
    ChannelSummaryMetrics,
    calculate_channel_metrics,
    compute_sample_market_share,
)
from echoscope.api.youtube_client import (
    EchoScopeAPIError,
    QuotaExceededError,
    YouTubeClient,
    normalize_handle,
)
from echoscope.config import get_settings
from echoscope.database.connection import get_db_connection
from echoscope.database.migrations import init_schema
from echoscope.database.models import (
    AnalysisRun,
    Channel,
    ChannelSnapshot,
    RunStatus,
    Video,
    VideoSnapshot,
)
from echoscope.database.repository import EchoScopeRepository


def _parse_date(val: Any) -> date:
    """Parses date, datetime, or ISO string into a standard datetime.date."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return datetime.fromisoformat(val[:10]).date()
    raise ValueError(f"Cannot parse date from: {val!r}")


class PipelineService:
    """Coordinates headless business logic and analytics without UI dependencies."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        conn: sqlite3.Connection | None = None,
        youtube_client: YouTubeClient | None = None,
        ai_client: GeminiAnalysisClient | None = None,
    ):
        if conn is not None:
            self.conn = conn
        else:
            self.conn = get_db_connection(db_path=db_path)

        # Ensure database tables exist
        init_schema(self.conn)

        self.repository = EchoScopeRepository(self.conn)
        self._youtube_client = youtube_client
        self._ai_client = ai_client

        # Reconcile any runs that were abruptly interrupted
        self.reconcile_interrupted_runs()

    @property
    def youtube_client(self) -> YouTubeClient:
        """Lazily initializes the YouTube client only when network collection is needed."""
        if self._youtube_client is None:
            self._youtube_client = YouTubeClient()
        return self._youtube_client

    @property
    def ai_client(self) -> GeminiAnalysisClient:
        """Lazily initializes the Gemini client only when AI generation is requested."""
        if self._ai_client is None:
            self._ai_client = GeminiAnalysisClient()
        return self._ai_client

    @property
    def is_youtube_configured(self) -> bool:
        """Returns True if YouTube API key is available and configured."""
        if self._youtube_client is not None and getattr(self._youtube_client, "api_key", None):
            return True
        return get_settings().is_youtube_configured

    @property
    def is_gemini_configured(self) -> bool:
        """Returns True if Gemini API key is available and configured."""
        if self._ai_client is not None and getattr(self._ai_client, "api_key", None):
            return True
        return get_settings().is_gemini_configured

    def reconcile_interrupted_runs(self) -> int:
        """Reconciles abandoned 'running' executions to 'partial' or 'failed'."""
        return self.repository.reconcile_interrupted_runs()

    def create_analysis_run(
        self,
        period_start: date,
        period_end: date,
        metrics_version: str = "1.0",
    ) -> str:
        """Initializes a new isolated analysis execution run."""
        if period_start > period_end:
            raise ValueError(
                f"Invalid date range: period_start ({period_start}) cannot be after period_end ({period_end})."
            )

        run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        run = AnalysisRun(
            run_id=run_id,
            period_start=period_start,
            period_end=period_end,
            started_at=datetime.now(UTC),
            finished_at=None,
            status="pending",
            metrics_version=metrics_version,
        )
        self.repository.create_run(run)
        return run_id

    def collect_channels(
        self,
        run_id: str,
        channel_inputs: list[str],
    ) -> dict[str, Any]:
        """Collects channel metadata and videos for the specified run.

        Tolerates partial failures: successfully collected channels are persisted and
        analyzed while failed channels are marked in analysis_run_channels.
        """
        run_info = self.repository.get_run(run_id)
        if not run_info:
            raise ValueError(f"Run ID '{run_id}' not found.")

        period_start = _parse_date(run_info["period_start"])
        period_end = _parse_date(run_info["period_end"])

        self.repository.update_run_status(run_id, "running")

        successful_channels: list[str] = []
        failed_channels: list[dict[str, str]] = []

        for ch_input in channel_inputs:
            ch_input = ch_input.strip()
            if not ch_input:
                continue

            try:
                resolved = self.youtube_client.resolve_channel(ch_input)
                channel = Channel(
                    channel_id=resolved.channel_id,
                    handle=resolved.handle,
                    title=resolved.title,
                    first_seen_at=datetime.now(UTC),
                )
                self.repository.associate_channel_run(run_id, channel, status="pending")

                # Snapshot subscribers at collection time
                snapshot = ChannelSnapshot(
                    run_id=run_id,
                    channel_id=resolved.channel_id,
                    subscribers_count=resolved.subscribers_count,
                    collected_at=datetime.now(UTC),
                )
                self.repository.record_channel_snapshot(snapshot)

                # Fetch videos and snapshots bounded in the period
                videos, vid_snapshots = self.youtube_client.fetch_channel_videos_for_period(
                    channel=resolved,
                    start_date=period_start,
                    end_date=period_end,
                    run_id=run_id,
                )

                if videos:
                    self.repository.save_video_batch(
                        run_id=run_id,
                        channel_id=resolved.channel_id,
                        videos=videos,
                        snapshots=vid_snapshots,
                    )
                else:
                    self.repository.associate_channel_run(run_id, channel, status="empty")

                successful_channels.append(resolved.handle)

            except QuotaExceededError as q_err:
                # Quota exhaustion affects all subsequent channels
                fallback_ch_id = normalize_handle(ch_input)
                self.repository.mark_channel_failed(run_id, fallback_ch_id, "QuotaExceededError")
                failed_channels.append(
                    {
                        "channel": ch_input,
                        "error": str(q_err),
                        "error_code": "QuotaExceededError",
                    }
                )
                break
            except EchoScopeAPIError as api_err:
                fallback_ch_id = normalize_handle(ch_input)
                self.repository.mark_channel_failed(run_id, fallback_ch_id, type(api_err).__name__)
                failed_channels.append(
                    {
                        "channel": ch_input,
                        "error": str(api_err),
                        "error_code": type(api_err).__name__,
                    }
                )
            except Exception as ex:
                fallback_ch_id = normalize_handle(ch_input)
                self.repository.mark_channel_failed(run_id, fallback_ch_id, type(ex).__name__)
                failed_channels.append(
                    {
                        "channel": ch_input,
                        "error": str(ex),
                        "error_code": type(ex).__name__,
                    }
                )

        # Determine terminal run status
        final_status: RunStatus
        if not successful_channels and failed_channels:
            final_status = "failed"
        elif successful_channels and failed_channels:
            final_status = "partial"
        else:
            final_status = "completed"

        self.repository.update_run_status(run_id, final_status)

        return {
            "run_id": run_id,
            "status": final_status,
            "total_channels": len(channel_inputs),
            "successful_channels": successful_channels,
            "failed_channels": failed_channels,
            "quota_used": self.youtube_client.quota_units_used,
        }

    def calculate_run_metrics(self, run_id: str) -> list[ChannelSummaryMetrics]:
        """Computes statistical metrics for all channels in the specified run."""
        run_info = self.repository.get_run(run_id)
        if not run_info:
            raise ValueError(f"Run ID '{run_id}' not found.")

        period_start = _parse_date(run_info["period_start"])
        period_end = _parse_date(run_info["period_end"])
        run_channels = self.repository.get_run_channels(run_id)

        metrics_list: list[ChannelSummaryMetrics] = []

        for ch in run_channels:
            if ch["status"] not in ("completed", "empty"):
                continue

            channel_id = ch["channel_id"]
            handle = ch["handle"]
            subs_count = ch.get("subscribers_count")

            videos_df = self.repository.get_run_channel_videos(run_id, channel_id)

            ch_metrics = calculate_channel_metrics(
                videos_df=videos_df,
                channel_id=channel_id,
                handle=handle,
                subscribers_count=subs_count,
                period_start=period_start,
                period_end=period_end,
                reference_collected_at=run_info.get("started_at"),
            )
            metrics_list.append(ch_metrics)

        return metrics_list

    def get_tracked_channel_views_share(self, run_id: str) -> list[dict[str, Any]]:
        """Calculates Share of Tracked Channel Views for competitive sample."""
        metrics_list = self.calculate_run_metrics(run_id)
        total_views, shares = compute_sample_market_share(metrics_list)
        return [
            {
                "channel_id": m.channel_id,
                "handle": m.handle,
                "views": m.total_views,
                "share_of_tracked_views_pct": shares.get(m.handle, 0.0),
                "total_sample_volume": total_views,
            }
            for m in metrics_list
        ]

    def get_run_market_share(self, run_id: str) -> list[dict[str, Any]]:
        """Alias for get_tracked_channel_views_share for backward compatibility."""
        return self.get_tracked_channel_views_share(run_id)

    def compare_channel_snapshots(
        self,
        channel_id: str,
        run_id_older: str,
        run_id_newer: str,
    ) -> ChannelGrowthComparison:
        """Compares two snapshots of a single channel across distinct runs."""
        run_older = self.repository.get_run(run_id_older)
        if not run_older:
            raise ValueError(f"Older run '{run_id_older}' not found.")

        run_newer = self.repository.get_run(run_id_newer)
        if not run_newer:
            raise ValueError(f"Newer run '{run_id_newer}' not found.")

        videos_older = self.repository.get_run_channel_videos(run_id_older, channel_id)
        videos_newer = self.repository.get_run_channel_videos(run_id_newer, channel_id)

        date_older = _parse_date(run_older["period_end"])
        date_newer = _parse_date(run_newer["period_end"])

        return compute_channel_growth(
            channel_id=channel_id,
            run_id_older=run_id_older,
            run_id_newer=run_id_newer,
            date_older=date_older,
            date_newer=date_newer,
            videos_df_older=videos_older,
            videos_df_newer=videos_newer,
        )

    def generate_channel_report(
        self,
        channel_id: str,
        run_id: str,
        top_n_videos: int = 5,
    ) -> EchoScopeReport:
        """Generates structured strategic analysis with Pydantic validation and fallback."""
        run_info = self.repository.get_run(run_id)
        if not run_info:
            raise ValueError(f"Run ID '{run_id}' not found.")

        period_start = _parse_date(run_info["period_start"])
        period_end = _parse_date(run_info["period_end"])

        channel = self.repository.get_channel(channel_id)
        handle = channel.handle if channel else channel_id

        # Retrieve subscriber count from snapshot
        run_channels = self.repository.get_run_channels(run_id)
        subscribers_count: int | None = None
        for r_ch in run_channels:
            if r_ch["channel_id"] == channel_id:
                subscribers_count = r_ch.get("subscribers_count")
                if channel is None:
                    handle = r_ch["handle"]
                break

        videos_df = self.repository.get_run_channel_videos(run_id, channel_id)
        metrics = calculate_channel_metrics(
            videos_df=videos_df,
            channel_id=channel_id,
            handle=handle,
            subscribers_count=subscribers_count,
            period_start=period_start,
            period_end=period_end,
            reference_collected_at=run_info.get("started_at"),
        )

        top_videos = videos_df.to_dict(orient="records")[:top_n_videos]
        return self.ai_client.generate_report(channel_metrics=metrics, top_videos=top_videos)

    def list_runs(self) -> list[dict[str, Any]]:
        """Lists historical execution runs."""
        return self.repository.list_runs()

    def list_channels(self) -> list[dict[str, Any]]:
        """Lists all registered channels in the repository."""
        channels = self.repository.list_channels()
        return [
            {
                "channel_id": c.channel_id,
                "handle": c.handle,
                "title": c.title,
            }
            for c in channels
        ]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Retrieves single run metadata."""
        return self.repository.get_run(run_id)

    def get_run_channels(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieves channels participating in a run."""
        return self.repository.get_run_channels(run_id)

    def get_channel_videos(self, run_id: str, channel_id: str) -> pd.DataFrame:
        """Retrieves video snapshots for a channel within a specific run."""
        return self.repository.get_run_channel_videos(run_id, channel_id)

    def get_run_overview(self, run_id: str) -> dict[str, Any]:
        """Provides a consolidated, offline-cached overview of a run without API calls."""
        run_info = self.repository.get_run(run_id)
        if not run_info:
            raise ValueError(f"Run ID '{run_id}' not found.")

        macro_data = self.repository.get_run_macro_data(run_id)
        metrics = self.calculate_run_metrics(run_id)
        share_data = self.get_tracked_channel_views_share(run_id)

        return {
            "run": run_info,
            "macro_data": macro_data,
            "metrics": metrics,
            "tracked_views_share": share_data,
            "market_share": share_data,
        }

    def seed_demo_dataset(self) -> str:
        """Seeds two realistic temporal snapshots with demo channels and video data.

        Allows instant offline demonstration and testing of all UI tabs without API credentials.
        Returns the newer run_id.
        """
        now = datetime.now(UTC)
        today = now.date()
        t0_start = today - timedelta(days=60)
        t0_end = today - timedelta(days=30)
        t1_start = today - timedelta(days=30)
        t1_end = today

        # 1. Run T0
        run0_id = f"demo_t0_{now.strftime('%m%d_%H%M%S')}"
        self.repository.create_run(
            AnalysisRun(
                run_id=run0_id,
                period_start=t0_start,
                period_end=t0_end,
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1),
                status="completed",
                metrics_version="1.0",
            )
        )

        # 2. Run T1
        run1_id = f"demo_t1_{now.strftime('%m%d_%H%M%S')}"
        self.repository.create_run(
            AnalysisRun(
                run_id=run1_id,
                period_start=t1_start,
                period_end=t1_end,
                started_at=now,
                finished_at=now,
                status="completed",
                metrics_version="1.0",
            )
        )

        demo_channels = [
            ("UC_BEAST", "@MrBeast", "MrBeast", 320_000_000, 325_000_000),
            ("UC_MKBHD", "@mkbhd", "Marques Brownlee", 19_500_000, 19_800_000),
            ("UC_VERIT", "@veritasium", "Veritasium", 16_000_000, 16_200_000),
        ]

        for ch_id, handle, title, subs_t0, subs_t1 in demo_channels:
            ch = Channel(channel_id=ch_id, handle=handle, title=title, first_seen_at=now)
            self.repository.upsert_channel(ch)
            self.repository.associate_channel_run(run0_id, ch, status="completed")
            self.repository.associate_channel_run(run1_id, ch, status="completed")
            self.repository.record_channel_snapshot(
                ChannelSnapshot(run_id=run0_id, channel_id=ch_id, subscribers_count=subs_t0)
            )
            self.repository.record_channel_snapshot(
                ChannelSnapshot(run_id=run1_id, channel_id=ch_id, subscribers_count=subs_t1)
            )

        # Videos for MrBeast
        vids_beast = [
            Video(
                video_id="vb_1",
                channel_id="UC_BEAST",
                title="$10,000,000 Challenge",
                published_at=now - timedelta(days=40),
                duration_seconds=1200,
            ),
            Video(
                video_id="vb_2",
                channel_id="UC_BEAST",
                title="Survive 100 Days in Nuclear Bunker",
                published_at=now - timedelta(days=25),
                duration_seconds=1800,
            ),
            Video(
                video_id="vb_3",
                channel_id="UC_BEAST",
                title="World's Most Dangerous Trap!",
                published_at=now - timedelta(days=10),
                duration_seconds=58,
            ),
        ]
        snaps_b0 = [
            VideoSnapshot(
                run_id=run0_id,
                channel_id="UC_BEAST",
                video_id="vb_1",
                views_count=45_000_000,
                likes_count=3_200_000,
                format_category="Long-form",
            ),
        ]
        snaps_b1 = [
            VideoSnapshot(
                run_id=run1_id,
                channel_id="UC_BEAST",
                video_id="vb_1",
                views_count=72_000_000,
                likes_count=4_500_000,
                format_category="Long-form",
            ),
            VideoSnapshot(
                run_id=run1_id,
                channel_id="UC_BEAST",
                video_id="vb_2",
                views_count=38_000_000,
                likes_count=2_800_000,
                format_category="Long-form",
            ),
            VideoSnapshot(
                run_id=run1_id,
                channel_id="UC_BEAST",
                video_id="vb_3",
                views_count=19_000_000,
                likes_count=1_200_000,
                format_category="Shorts",
            ),
        ]
        self.repository.save_video_batch(run0_id, "UC_BEAST", [vids_beast[0]], snaps_b0)
        self.repository.save_video_batch(run1_id, "UC_BEAST", vids_beast, snaps_b1)

        # Videos for MKBHD
        vids_mkbhd = [
            Video(
                video_id="vm_1",
                channel_id="UC_MKBHD",
                title="Smartphone of the Year 2026",
                published_at=now - timedelta(days=35),
                duration_seconds=950,
            ),
            Video(
                video_id="vm_2",
                channel_id="UC_MKBHD",
                title="The Ultimate EV Review",
                published_at=now - timedelta(days=12),
                duration_seconds=1100,
            ),
        ]
        snaps_m0 = [
            VideoSnapshot(
                run_id=run0_id,
                channel_id="UC_MKBHD",
                video_id="vm_1",
                views_count=3_200_000,
                likes_count=180_000,
                format_category="Long-form",
            ),
        ]
        snaps_m1 = [
            VideoSnapshot(
                run_id=run1_id,
                channel_id="UC_MKBHD",
                video_id="vm_1",
                views_count=4_500_000,
                likes_count=240_000,
                format_category="Long-form",
            ),
            VideoSnapshot(
                run_id=run1_id,
                channel_id="UC_MKBHD",
                video_id="vm_2",
                views_count=2_100_000,
                likes_count=130_000,
                format_category="Long-form",
            ),
        ]
        self.repository.save_video_batch(run0_id, "UC_MKBHD", [vids_mkbhd[0]], snaps_m0)
        self.repository.save_video_batch(run1_id, "UC_MKBHD", vids_mkbhd, snaps_m1)

        # Videos for Veritasium
        vids_verit = [
            Video(
                video_id="vv_1",
                channel_id="UC_VERIT",
                title="The Math Paradox That Broke Science",
                published_at=now - timedelta(days=45),
                duration_seconds=1500,
            ),
            Video(
                video_id="vv_2",
                channel_id="UC_VERIT",
                title="Why Light Bends Around Corners",
                published_at=now - timedelta(days=15),
                duration_seconds=1350,
            ),
        ]
        snaps_v0 = [
            VideoSnapshot(
                run_id=run0_id,
                channel_id="UC_VERIT",
                video_id="vv_1",
                views_count=5_100_000,
                likes_count=310_000,
                format_category="Long-form",
            ),
        ]
        snaps_v1 = [
            VideoSnapshot(
                run_id=run1_id,
                channel_id="UC_VERIT",
                video_id="vv_1",
                views_count=6_800_000,
                likes_count=390_000,
                format_category="Long-form",
            ),
            VideoSnapshot(
                run_id=run1_id,
                channel_id="UC_VERIT",
                video_id="vv_2",
                views_count=3_400_000,
                likes_count=220_000,
                format_category="Long-form",
            ),
        ]
        self.repository.save_video_batch(run0_id, "UC_VERIT", [vids_verit[0]], snaps_v0)
        self.repository.save_video_batch(run1_id, "UC_VERIT", vids_verit, snaps_v1)

        return run1_id
