"""Decoupled Streamlit application for EchoScope AI.

Consumes exclusively the PipelineService business layer. Contains NO direct database
connections, NO raw SQL queries, and NO direct external API clients.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from echoscope.services.pipeline_service import PipelineService, _parse_date
from ui.components.cards import render_channel_kpis, render_metric_card
from ui.components.charts import (
    render_format_breakdown_chart,
    render_market_share_pie,
    render_video_scatter_chart,
    render_views_bar_chart,
)
from ui.components.tables import render_channels_table, render_videos_table

# Page Configuration
st.set_page_config(
    layout="wide",
    page_title="EchoScope AI — YouTube Competitive Intelligence",
    page_icon="🔴",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_service() -> PipelineService:
    """Returns singleton pipeline service instance."""
    return PipelineService()


def main() -> None:
    service = get_service()

    if "active_run_id" not in st.session_state:
        st.session_state["active_run_id"] = None

    # --- SIDEBAR: NAVIGATION & EXECUTION ---
    with st.sidebar:
        st.title("🔴 EchoScope AI")
        st.caption("YouTube Competitive Intelligence & Analytics")
        st.divider()

        # API Status & Session Configuration
        with st.expander("🔑 API Credentials & Status", expanded=not service.is_youtube_configured):
            yt_ok = service.is_youtube_configured
            ai_ok = service.is_gemini_configured
            st.markdown(f"- **YouTube Data API:** {'✅ Configured' if yt_ok else '❌ Missing'}")
            st.markdown(f"- **Gemini AI API:** {'✅ Configured' if ai_ok else '❌ Missing'}")

            if not yt_ok or not ai_ok:
                st.caption(
                    "Add keys to `.env` (see `.env.example`) or provide them below for this session:"
                )
                input_yt = st.text_input("YouTube API Key:", type="password", key="input_yt_key")
                input_ai = st.text_input("Gemini API Key:", type="password", key="input_ai_key")
                if st.button("Apply Session Keys", use_container_width=True):
                    import os

                    if input_yt.strip():
                        os.environ["YOUTUBE_API_KEY"] = input_yt.strip()
                    if input_ai.strip():
                        os.environ["GEMINI_API_KEY"] = input_ai.strip()
                    st.cache_resource.clear()
                    st.success("Session API keys updated!")
                    st.rerun()

            st.divider()
            st.caption("Explore all features offline without API keys:")
            if st.button("✨ Load Demo Dataset", use_container_width=True):
                with st.spinner("Seeding demo snapshot datasets..."):
                    demo_id = service.seed_demo_dataset()
                    st.session_state["active_run_id"] = demo_id
                    st.session_state["collection_feedback"] = {
                        "type": "success",
                        "message": f"Demo dataset loaded successfully! Run ID: `{demo_id}`",
                    }
                    st.rerun()

        st.divider()

        nav_mode = st.radio(
            "Operation Mode",
            ["New Collection", "Historical Runs"],
            index=0,
        )

        st.divider()

        if nav_mode == "New Collection":
            st.subheader("Configure Collection")
            if not service.is_youtube_configured:
                st.warning(
                    "⚠️ **YouTube API key not configured.** Live collections require `YOUTUBE_API_KEY`. "
                    "Configure it in `.env` or in the API panel above, or click '✨ Load Demo Dataset' to explore offline."
                )

            channels_input = st.text_area(
                "Channels (handles or IDs, comma or line separated):",
                value="@MrBeast, @mkbhd, @veritasium",
                help="Input handles like @MrBeast or IDs like UCxxxx",
            )

            col_d1, col_d2 = st.columns(2)
            default_end = datetime.now(UTC).date()
            default_start = default_end - timedelta(days=30)
            with col_d1:
                start_date = st.date_input("Start Date", value=default_start)
            with col_d2:
                end_date = st.date_input("End Date", value=default_end)

            start_btn = st.button("🚀 Start Collection", use_container_width=True, type="primary")

            if start_btn:
                raw_channels = [
                    c.strip() for c in channels_input.replace("\n", ",").split(",") if c.strip()
                ]
                if not raw_channels:
                    st.error("Please provide at least one valid channel.")
                elif start_date > end_date:
                    st.error("Start date cannot be after end date.")
                elif not service.is_youtube_configured:
                    st.error(
                        "Cannot start collection: YouTube API key is missing. "
                        "Please configure YOUTUBE_API_KEY in `.env` or in the API Credentials panel above."
                    )
                else:
                    with st.spinner("Executing resilient collection and snapshot isolation..."):
                        try:
                            s_date = _parse_date(start_date)
                            e_date = _parse_date(end_date)
                            run_id = service.create_analysis_run(
                                period_start=s_date, period_end=e_date
                            )
                            summary = service.collect_channels(
                                run_id=run_id, channel_inputs=raw_channels
                            )
                            st.session_state["active_run_id"] = run_id

                            if summary["status"] == "completed":
                                st.session_state["collection_feedback"] = {
                                    "type": "success",
                                    "message": f"Collection completed successfully! Run ID: `{run_id}`",
                                    "details": summary,
                                }
                            elif summary["status"] == "partial":
                                st.session_state["collection_feedback"] = {
                                    "type": "warning",
                                    "message": (
                                        f"Partial collection: {len(summary['successful_channels'])} succeeded, "
                                        f"{len(summary['failed_channels'])} failed. Run ID: `{run_id}`"
                                    ),
                                    "details": summary,
                                }
                            else:
                                st.session_state["collection_feedback"] = {
                                    "type": "error",
                                    "message": f"Collection failed for all target channels. Run ID: `{run_id}`",
                                    "details": summary,
                                }
                            st.rerun()
                        except Exception as e:
                            st.session_state["collection_feedback"] = {
                                "type": "error",
                                "message": f"Execution error: {e}",
                            }
                            st.rerun()

        else:
            st.subheader("Execution History")
            runs = service.list_runs()
            if not runs:
                st.info("No runs recorded in database yet.")
            else:
                options = {
                    f"{r['run_id']} ({r.get('period_start', '')[:10]} .. {r.get('period_end', '')[:10]} - {r['status']})": r[
                        "run_id"
                    ]
                    for r in runs
                }
                selected_label = st.selectbox("Select an execution run:", list(options.keys()))
                if selected_label and selected_label in options:
                    st.session_state["active_run_id"] = options[selected_label]

    # --- MAIN CONTENT AREA ---
    # Display any collection feedback from previous action
    feedback = st.session_state.get("collection_feedback")
    if feedback:
        fb_type = feedback.get("type", "info")
        fb_msg = feedback.get("message", "")
        if fb_type == "success":
            st.success(fb_msg)
        elif fb_type == "warning":
            st.warning(fb_msg)
        else:
            st.error(fb_msg)

        details = feedback.get("details", {})
        if details.get("failed_channels"):
            with st.expander("🔍 View Channel Failure Details", expanded=True):
                for f in details["failed_channels"]:
                    st.markdown(
                        f"- **{f.get('channel')}**: `[{f.get('error_code')}]` {f.get('error')}"
                    )

        if st.button("Dismiss Notification", key="dismiss_fb"):
            st.session_state.pop("collection_feedback", None)
            st.rerun()

    active_run_id = st.session_state.get("active_run_id")

    if not active_run_id:
        st.info("👈 Select a historical run or launch a new collection from the sidebar.")
        return

    try:
        overview = service.get_run_overview(active_run_id)
    except Exception as e:
        st.error(f"Error loading run data for '{active_run_id}': {e}")
        return

    run_meta = overview["run"]
    macro_data = overview["macro_data"]
    metrics_list = overview["metrics"]
    market_share = overview.get("tracked_views_share", overview.get("market_share", []))

    # Header
    st.subheader(f"Execution Run: `{active_run_id}`")
    st.caption(
        f"Monitoring Window: {run_meta.get('period_start')} to {run_meta.get('period_end')} | "
        f"Status: **{run_meta.get('status', '').upper()}** | "
        f"Metrics Engine: v{run_meta.get('metrics_version', '1.0')}"
    )

    if run_meta.get("status") == "failed":
        st.error(
            f"Execution run `{active_run_id}` ended with status **FAILED**. No video metrics were collected."
        )
        run_chans = service.get_run_channels(active_run_id)
        if run_chans:
            st.markdown("#### Channel Collection Breakdown")
            for ch in run_chans:
                err = ch.get("error_code") or "Unknown error"
                st.markdown(
                    f"- **{ch.get('handle', ch.get('channel_id'))}**: ❌ `{ch.get('status')}` ({err})"
                )
        st.info(
            "💡 To collect data, make sure your YouTube Data API key is configured with active quota."
        )
        return

    tab_macro, tab_videos, tab_growth, tab_ai = st.tabs(
        [
            "📊 Market Overview",
            "🎬 Video Explorer",
            "📈 Growth Signals",
            "🤖 Strategic AI Report",
        ]
    )

    # ---------------- TAB 1: MACRO & MARKET SHARE ----------------
    with tab_macro:
        st.markdown("### Sample Competitive Overview")
        if not metrics_list:
            st.info("No completed channel metrics available for this run.")
        else:
            col_m1, col_m2, col_m3 = st.columns(3)
            total_tracked_views = sum(m.total_views for m in metrics_list)
            total_sample_videos = sum(m.sample_size_videos for m in metrics_list)

            with col_m1:
                render_metric_card("Tracked Channels", len(metrics_list))
            with col_m2:
                render_metric_card("Analyzed Videos (n)", f"{total_sample_videos:,}")
            with col_m3:
                render_metric_card("Total Sample Views", f"{total_tracked_views:,}")

            st.divider()

            c_chart1, c_chart2 = st.columns(2)
            with c_chart1:
                render_views_bar_chart(macro_data)
            with c_chart2:
                render_market_share_pie(market_share)

            st.divider()
            st.markdown("### Channel Breakdown")
            render_channels_table(macro_data)

            st.divider()
            st.markdown("### Publication Format Distribution")
            render_format_breakdown_chart(metrics_list)

    # ---------------- TAB 2: VIDEO EXPLORER ----------------
    with tab_videos:
        st.markdown("### Granular Video Performance")
        if not metrics_list:
            st.info("No channels with collected videos in this run.")
        else:
            channel_map = {m.handle: m.channel_id for m in metrics_list if m.handle}
            if not channel_map:
                st.info("No channels available to inspect in this run.")
            else:
                selected_handle = st.selectbox(
                    "Select Channel to Inspect:", list(channel_map.keys())
                )
                selected_ch_id = channel_map.get(selected_handle) if selected_handle else None

                if not selected_ch_id:
                    st.info("Please select a channel to inspect.")
                else:
                    # Channel KPIs
                    selected_metrics = next(
                        (m for m in metrics_list if m.channel_id == selected_ch_id), None
                    )
                    if selected_metrics:
                        render_channel_kpis(selected_metrics)
                        st.divider()

                    videos_df = service.get_channel_videos(active_run_id, selected_ch_id)

                    if videos_df.empty:
                        st.info("No videos published within the period for this channel.")
                    else:
                        available_cats = list(videos_df["category"].dropna().unique())
                        selected_cats = st.multiselect(
                            "Filter by Format:",
                            options=available_cats,
                            default=available_cats,
                        )
                        filtered_df = pd.DataFrame(
                            videos_df[videos_df["category"].isin(selected_cats)]
                        )

                        render_video_scatter_chart(filtered_df)
                        st.divider()
                        st.markdown("#### Video Catalog")
                        render_videos_table(filtered_df)

    # ---------------- TAB 3: GROWTH SIGNALS ----------------
    with tab_growth:
        st.markdown("### Inter-Snapshot Growth Signals")
        st.caption(
            "Measures daily growth velocity (R) and net views variation (ΔV) for identical videos across distinct snapshots."
        )

        runs = service.list_runs()
        if len(runs) < 2:
            st.info(
                "At least two execution runs are required in history to calculate growth signals."
            )
        else:
            col_g1, col_g2, col_g3 = st.columns(3)
            run_opts = [r["run_id"] for r in runs]
            with col_g2:
                run_t0 = st.selectbox(
                    "Older Snapshot (T0):", run_opts, index=min(1, len(run_opts) - 1)
                )

            with col_g3:
                run_t1 = st.selectbox("Recent Snapshot (T1):", run_opts, index=0)

            # Discover channels present in the selected runs
            channels_t0 = (
                {
                    ch["channel_id"]: ch.get("handle") or ch["channel_id"]
                    for ch in service.get_run_channels(str(run_t0))
                }
                if run_t0
                else {}
            )
            channels_t1 = (
                {
                    ch["channel_id"]: ch.get("handle") or ch["channel_id"]
                    for ch in service.get_run_channels(str(run_t1))
                }
                if run_t1
                else {}
            )
            common_ids = set(channels_t0.keys()).intersection(channels_t1.keys())

            if common_ids:
                channel_map = {channels_t1[cid]: cid for cid in common_ids}
            elif channels_t0 or channels_t1:
                all_ids = set(channels_t0.keys()) | set(channels_t1.keys())
                channel_map = {
                    channels_t1.get(cid, channels_t0.get(cid, cid)): cid for cid in all_ids
                }
            else:
                registered = service.list_channels()
                channel_map = {c["handle"]: c["channel_id"] for c in registered}

            with col_g1:
                if not channel_map:
                    st.info("No channels found in selected snapshots.")
                    g_channel_id = None
                else:
                    g_channel_handle = st.selectbox(
                        "Target Channel:", list(channel_map.keys()), key="growth_channel"
                    )
                    g_channel_id = channel_map.get(g_channel_handle) if g_channel_handle else None

            btn_compare = st.button("⚡ Calculate Growth Velocity", type="primary")

            if btn_compare:
                if not g_channel_id:
                    st.warning("Please select a valid channel to calculate growth signals.")
                elif not run_t0 or not run_t1 or run_t0 == run_t1:
                    st.warning("Please select two distinct runs to compare temporal evolution.")
                else:
                    try:
                        growth = service.compare_channel_snapshots(
                            channel_id=g_channel_id,
                            run_id_older=str(run_t0),
                            run_id_newer=str(run_t1),
                        )

                        col_k1, col_k2, col_k3, col_k4 = st.columns(4)
                        with col_k1:
                            render_metric_card(
                                "Daily Velocity (R)",
                                f"{growth.daily_growth_velocity:,.0f} views/day",
                                help_text="Net daily pace of views gained across comparable videos",
                            )
                        with col_k2:
                            render_metric_card(
                                "Gross Positive Growth",
                                f"+{growth.gross_positive_growth:,}",
                                help_text="Total views gained across growing comparable videos",
                            )
                        with col_k3:
                            render_metric_card(
                                "Negative View Count Adjustments (-)",
                                f"-{growth.negative_adjustments_views:,}",
                                help_text="Absolute sum of view count decreases across comparable videos between snapshots",
                            )
                        with col_k4:
                            render_metric_card(
                                "Comparable Videos",
                                growth.comparable_videos_count,
                                help_text="Videos present in both point-in-time snapshots",
                            )

                        st.divider()
                        st.markdown(
                            f"**New uploads published between T0 and T1:** {growth.new_videos_published_count} "
                            f"(totaling {growth.new_videos_total_views:,} views)"
                        )
                        if growth.unmatched_historical_videos_count > 0:
                            st.caption(
                                f"Historical videos omitted in T0: {growth.unmatched_historical_videos_count}"
                            )

                    except Exception as ex:
                        st.error(f"Error computing growth signals: {ex}")

    # ---------------- TAB 4: AI STRATEGIC REPORT ----------------
    with tab_ai:
        st.markdown("### Automated Strategic Diagnosis (Gemini)")
        st.caption(
            "Evidence-based qualitative synthesis strictly grounded in pre-calculated deterministic metrics."
        )

        if not metrics_list:
            st.info("No channels available for strategic analysis in this run.")
        else:
            channel_map = {m.handle: m.channel_id for m in metrics_list if m.handle}
            if not channel_map:
                st.info("No valid channels available for strategic analysis in this run.")
            else:
                ai_ch_handle = st.selectbox(
                    "Select Channel for Strategic Analysis:",
                    list(channel_map.keys()),
                    key="ai_channel_select",
                )
                ai_ch_id = channel_map.get(ai_ch_handle) if ai_ch_handle else None

                generate_ai_btn = st.button("🧠 Generate Strategic Report", type="primary")

                if generate_ai_btn:
                    if not ai_ch_id:
                        st.warning("Please select a valid channel to analyze.")
                    else:
                        with st.spinner(
                            "Generating structured diagnosis with Pydantic verification..."
                        ):
                            try:
                                report = service.generate_channel_report(
                                    channel_id=ai_ch_id, run_id=active_run_id
                                )

                                st.success(
                                    f"Report generated successfully! (Model: `{report.model_used}` | Prompt: `v{report.prompt_version}`)"
                                )

                                # 1. Observed Data
                                st.markdown("#### 1. Observed Data & Verified Metrics")
                                for obs in report.observed_data:
                                    st.info(f"**[{obs.metric_id}]**: {obs.interpretation}")

                                # 2. Performance Patterns
                                st.markdown("#### 2. Performance Patterns Identified")
                                for pat in report.performance_patterns:
                                    with st.expander(f"📌 {pat.pattern_title}", expanded=True):
                                        st.write(pat.observation)
                                        st.caption(
                                            f"Substantiating Metrics: {', '.join(pat.supporting_metric_ids)}"
                                        )

                                # 3. Strategic Hypotheses
                                st.markdown(
                                    "#### 3. Strategic Hypotheses & Tactical Recommendations"
                                )
                                for hyp in report.strategic_hypotheses:
                                    with st.container():
                                        st.markdown(
                                            f"**Hypothesis ({hyp.confidence_level.upper()} Confidence):** {hyp.statement}"
                                        )
                                        if hyp.supporting_metrics:
                                            st.caption(
                                                f"Empirical Metrics: {', '.join(hyp.supporting_metrics)}"
                                            )
                                        if hyp.limitations:
                                            st.caption(
                                                f"Data Caveats: {', '.join(hyp.limitations)}"
                                            )
                                        st.divider()

                                # 4. Study Limitations
                                st.markdown("#### 4. Methodological Study Limitations")
                                for lim in report.study_limitations:
                                    st.warning(f"⚠️ {lim}")

                            except Exception as err:
                                st.error(f"Error generating AI report: {err}")


if __name__ == "__main__":
    main()
