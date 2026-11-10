"""Headless Command-Line Interface (CLI) for EchoScope AI."""

import argparse
import sys
from collections.abc import Sequence

from echoscope.services.pipeline_service import PipelineService, _parse_date

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_ARGUMENTS = 2
EXIT_PARTIAL = 3


def _build_parser() -> argparse.ArgumentParser:
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument("--db", type=str, default=None, help="Path to SQLite database file")

    parser = argparse.ArgumentParser(
        prog="echoscope",
        description="EchoScope AI — Headless YouTube Competitive Intelligence CLI",
        parents=[base_parser],
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Sub-commands")

    # Command: collect
    collect_parser = subparsers.add_parser(
        "collect",
        parents=[base_parser],
        help="Execute an isolated collection and snapshot run",
    )
    collect_parser.add_argument(
        "--channels",
        nargs="+",
        required=True,
        help="One or more YouTube handles or channel IDs (e.g. @MrBeast @veritasium)",
    )
    collect_parser.add_argument(
        "--start",
        required=True,
        help="Period start date (YYYY-MM-DD)",
    )
    collect_parser.add_argument(
        "--end",
        required=True,
        help="Period end date (YYYY-MM-DD)",
    )

    # Command: list-runs
    subparsers.add_parser(
        "list-runs",
        parents=[base_parser],
        help="List all completed and partial analysis runs",
    )

    # Command: overview
    overview_parser = subparsers.add_parser(
        "overview",
        parents=[base_parser],
        help="Display macro metrics and market share for a run",
    )
    overview_parser.add_argument("--run-id", required=True, help="Analysis run identifier")

    # Command: report
    report_parser = subparsers.add_parser(
        "report",
        parents=[base_parser],
        help="Generate structured strategic AI analysis for a channel",
    )
    report_parser.add_argument("--run-id", required=True, help="Analysis run identifier")
    report_parser.add_argument("--channel-id", required=True, help="Target YouTube channel ID")
    report_parser.add_argument(
        "--top-videos", type=int, default=5, help="Number of top videos to include as context"
    )

    # Command: compare
    compare_parser = subparsers.add_parser(
        "compare",
        parents=[base_parser],
        help="Compare channel growth velocity between two snapshot runs",
    )
    compare_parser.add_argument("--channel-id", required=True, help="Target YouTube channel ID")
    compare_parser.add_argument("--older", required=True, help="Older run_id (T0)")
    compare_parser.add_argument("--newer", required=True, help="Newer run_id (T1)")

    return parser


def handle_collect(service: PipelineService, args: argparse.Namespace) -> int:
    try:
        start_d = _parse_date(args.start)
        end_d = _parse_date(args.end)
    except Exception as e:
        print(f"Error: Invalid date format: {e}", file=sys.stderr)
        return EXIT_INVALID_ARGUMENTS

    print(f"[*] Initializing analysis run for period {start_d} to {end_d}...")
    run_id = service.create_analysis_run(period_start=start_d, period_end=end_d)
    print(f"[*] Run created with ID: {run_id}")

    print(f"[*] Starting collection across {len(args.channels)} channel(s)...")
    summary = service.collect_channels(run_id=run_id, channel_inputs=args.channels)

    print("\n================== COLLECTION SUMMARY ==================")
    print(f"Run ID:        {summary['run_id']}")
    print(f"Final Status:  {summary['status'].upper()}")
    print(f"Successful:    {len(summary['successful_channels'])} channels")
    for s_ch in summary["successful_channels"]:
        print(f"  ✓ {s_ch}")
    if summary["failed_channels"]:
        print(f"Failed:        {len(summary['failed_channels'])} channels")
        for f_ch in summary["failed_channels"]:
            print(f"  ✗ {f_ch['channel']}: {f_ch['error']}")
    print(f"Quota Units:   {summary['quota_used']}")
    print("========================================================\n")
    if summary["status"] == "completed":
        return EXIT_SUCCESS
    elif summary["status"] == "partial":
        return EXIT_PARTIAL
    return EXIT_ERROR


def handle_list_runs(service: PipelineService, _args: argparse.Namespace) -> int:
    runs = service.list_runs()
    if not runs:
        print("No analysis runs recorded yet.")
        return 0

    print(f"{'RUN ID':<35} | {'PERIOD':<23} | {'STATUS':<10} | {'CHANNELS':<8} | {'VIDEOS':<8}")
    print("-" * 92)
    for r in runs:
        period = f"{r.get('period_start', '')[:10]} .. {r.get('period_end', '')[:10]}"
        print(
            f"{r['run_id']:<35} | {period:<23} | {r['status']:<10} | "
            f"{r.get('total_channels', 0):<8} | {r.get('total_videos', 0):<8}"
        )
    return 0


def handle_overview(service: PipelineService, args: argparse.Namespace) -> int:
    try:
        overview = service.get_run_overview(args.run_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    run = overview["run"]
    print(f"\n=== MACRO OVERVIEW: {run['run_id']} ===")
    print(f"Period: {run['period_start']} to {run['period_end']} (Status: {run['status']})\n")

    metrics = overview["metrics"]
    if not metrics:
        print("No channel metrics available for this run.")
        return 0

    print(
        f"{'CHANNEL':<25} | {'VIDEOS (n)':<10} | {'MEDIAN VIEWS':<14} | {'AVG VPD':<10} | {'VSR':<8} | {'LIKES %':<8}"
    )
    print("-" * 85)
    for m in metrics:
        vsr_str = f"{m.views_to_subscribers_ratio:.2f}" if m.views_to_subscribers_ratio else "N/A"
        likes_str = f"{m.likes_to_views_ratio_pct:.1f}%" if m.likes_to_views_ratio_pct else "N/A"
        print(
            f"{m.handle:<25} | {m.sample_size_videos:<10} | {m.median_views:<14,.0f} | "
            f"{m.average_views_per_day:<10,.0f} | {vsr_str:<8} | {likes_str:<8}"
        )

    market_shares = overview.get("tracked_views_share", overview.get("market_share", []))
    if market_shares:
        print("\n=== SHARE OF TRACKED CHANNEL VIEWS (%) ===")
        for s in market_shares:
            print(
                f"  {s['handle']:<25}: {s['share_of_tracked_views_pct']:>5.1f}% ({s['views']:,} views)"
            )
    print()
    return 0


def handle_report(service: PipelineService, args: argparse.Namespace) -> int:
    print(f"[*] Generating strategic AI analysis for channel '{args.channel_id}'...")
    try:
        report = service.generate_channel_report(
            channel_id=args.channel_id,
            run_id=args.run_id,
            top_n_videos=args.top_videos,
        )
    except Exception as e:
        print(f"Error generating report: {e}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print(f"ECHOSCOPE STRATEGIC REPORT: {report.channel_handle}")
    print(f"Model: {report.model_used} | Prompt Version: {report.prompt_version}")
    print("=" * 60)

    print("\n### 1. OBSERVED DATA (REFERENCED METRICS)")
    for item in report.observed_data:
        val_tag = f" [Python Verified: {item.verified_value}]" if item.verified_value else ""
        print(f"- [{item.metric_id}]{val_tag}: {item.interpretation}")

    print("\n### 2. PERFORMANCE PATTERNS")
    for pat in report.performance_patterns:
        print(f"- Pattern: {pat.pattern_title}")
        print(f"  Observation: {pat.observation}")
        print(f"  Supporting Metrics: {', '.join(pat.supporting_metric_ids)}")

    print("\n### 3. STRATEGIC HYPOTHESES")
    for hyp in report.strategic_hypotheses:
        print(f"- Statement ({hyp.confidence_level.upper()} CONFIDENCE): {hyp.statement}")
        if hyp.supporting_metrics:
            print(f"  Metrics: {', '.join(hyp.supporting_metrics)}")
        if hyp.limitations:
            print(f"  Caveats: {', '.join(hyp.limitations)}")

    print("\n### 4. STUDY LIMITATIONS")
    for lim in report.study_limitations:
        print(f"- {lim}")
    print("=" * 60 + "\n")
    return 0


def handle_compare(service: PipelineService, args: argparse.Namespace) -> int:
    try:
        growth = service.compare_channel_snapshots(
            channel_id=args.channel_id,
            run_id_older=args.older,
            run_id_newer=args.newer,
        )
    except Exception as e:
        print(f"Error computing growth comparison: {e}", file=sys.stderr)
        return 1

    print("\n================ GROWTH SIGNALS COMPARISON ================")
    print(f"Channel ID:                {growth.channel_id}")
    print(f"T0 Run:                    {growth.run_id_older}")
    print(f"T1 Run:                    {growth.run_id_newer}")
    print(f"Days Between Runs:         {growth.days_between_runs} day(s)")
    print(f"Comparable Videos:         {growth.comparable_videos_count}")
    print(f"Gross Positive Growth:     +{growth.gross_positive_growth:,} views")
    print(f"Negative Adjustments:      -{growth.negative_adjustments_views:,} views")
    print(f"Net Growth Views:          {growth.net_growth_views:,} views")
    print(f"Daily Growth Velocity (R): {growth.daily_growth_velocity:,.1f} views/day")
    if growth.overall_growth_rate_pct is not None:
        print(f"Growth Rate:               {growth.overall_growth_rate_pct:+.2f}%")
    print(
        f"New Uploads (in period):   {growth.new_videos_published_count} videos ({growth.new_videos_total_views:,} views)"
    )
    print(f"Missing Historical Videos: {growth.unmatched_historical_videos_count}")
    print("===========================================================\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    service = PipelineService(db_path=args.db)

    handlers = {
        "collect": handle_collect,
        "list-runs": handle_list_runs,
        "overview": handle_overview,
        "report": handle_report,
        "compare": handle_compare,
    }

    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(service, args)


if __name__ == "__main__":
    sys.exit(main())
