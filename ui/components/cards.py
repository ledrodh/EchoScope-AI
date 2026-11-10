"""KPI and metric card display components."""

from typing import Any

import streamlit as st

from echoscope.analytics.metrics import ChannelSummaryMetrics


def render_metric_card(
    label: str,
    value: Any,
    help_text: str | None = None,
    delta: str | None = None,
) -> None:
    """Renders a standard Streamlit metric component safely."""
    st.metric(label=label, value=value, delta=delta, help=help_text)


def render_channel_kpis(metrics: ChannelSummaryMetrics) -> None:
    """Renders high-level analytical KPI cards for a channel."""
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        st.metric(
            label="Median Views",
            value=f"{metrics.median_views:,.0f}",
            help=f"Typical performance per video (sample size: {metrics.sample_size_videos} videos)",
        )

    with c2:
        st.metric(
            label="Average Views",
            value=f"{metrics.average_views:,.0f}",
            help="Arithmetic mean (susceptible to viral outliers)",
        )

    with c3:
        vsr_val = (
            f"{metrics.views_to_subscribers_ratio:.2f}"
            if metrics.views_to_subscribers_ratio is not None
            else "Hidden"
        )
        st.metric(
            label="VSR (Views/Subs)",
            value=vsr_val,
            help="Views-to-Subscriber Ratio (sampled views relative to subscriber count)",
        )

    with c4:
        likes_val = (
            f"{metrics.likes_to_views_ratio_pct:.1f}%"
            if metrics.likes_to_views_ratio_pct is not None
            else "N/A"
        )
        st.metric(
            label="Likes-to-Views Ratio",
            value=likes_val,
            help=f"Weighted engagement (Likes / Views across {metrics.likes_sample_size_videos} videos with public likes)",
        )

    with c5:
        st.metric(
            label="Upload Cadence",
            value=f"{metrics.upload_frequency_per_week:.1f}/wk",
            help="Average uploads per week during the analyzed monitoring window",
        )
