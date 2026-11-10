"""Plotly visualization components for competitive analysis."""

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from echoscope.analytics.metrics import ChannelSummaryMetrics


def render_views_bar_chart(macro_df: pd.DataFrame) -> None:
    """Renders horizontal or vertical bar chart comparing total channel views."""
    if macro_df.empty or "Channel" not in macro_df.columns:
        st.info("No data available for views chart.")
        return

    fig = px.bar(
        macro_df,
        x="Channel",
        y="Total_Views",
        title="Total Analyzed Video Views by Channel",
        labels={"Channel": "Channel", "Total_Views": "Total Views"},
        color="Total_Views",
        color_continuous_scale="Reds",
        template="plotly_dark",
    )
    fig.update_layout(xaxis_tickangle=-45, height=380, margin=dict(l=20, r=20, t=50, b=50))
    st.plotly_chart(fig, use_container_width=True)


def render_market_share_pie(market_share: list[dict[str, Any]]) -> None:
    """Renders donut chart for sample market share."""
    if not market_share:
        st.info("No tracked views share data available.")
        return

    df = pd.DataFrame(market_share)
    fig = px.pie(
        df,
        names="handle",
        values="views",
        title="Share of Tracked Channel Views (%)",
        hole=0.45,
        template="plotly_dark",
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(height=380, margin=dict(l=20, r=20, t=50, b=50))
    st.plotly_chart(fig, use_container_width=True)


def render_format_breakdown_chart(metrics_list: list[ChannelSummaryMetrics]) -> None:
    """Renders stacked bar chart comparing format distribution (Shorts vs Long vs Unknown)."""
    rows = []
    for m in metrics_list:
        rows.append(
            {
                "Channel": m.handle,
                "Shorts": m.format_breakdown.shorts_count,
                "Long-form": m.format_breakdown.long_form_count,
                "Unknown": m.format_breakdown.unknown_count,
            }
        )

    if not rows:
        return

    df = pd.DataFrame(rows)
    df_melted = df.melt(id_vars=["Channel"], var_name="Format", value_name="Videos")

    fig = px.bar(
        df_melted,
        x="Channel",
        y="Videos",
        color="Format",
        title="Publication Format Distribution (Shorts vs Long-form vs Unknown)",
        barmode="stack",
        color_discrete_map={
            "Shorts": "#E50914",
            "Long-form": "#4285F4",
            "Unknown": "#757575",
        },
        template="plotly_dark",
    )
    fig.update_layout(xaxis_tickangle=-45, height=360, margin=dict(l=20, r=20, t=50, b=50))
    st.plotly_chart(fig, use_container_width=True)


def render_video_scatter_chart(videos_df: pd.DataFrame) -> None:
    """Renders scatter plot of video views over publication timeline."""
    if videos_df.empty:
        st.info("No videos available for publication timeline.")
        return

    plot_df = videos_df.copy()
    fig = px.scatter(
        plot_df,
        x="date",
        y="views",
        color="category",
        size="views",
        hover_data=["title", "likes"],
        title="Video Performance Over Time",
        labels={"date": "Publication Date", "views": "Views", "category": "Format"},
        color_discrete_map={
            "Shorts": "#E50914",
            "Long-form": "#4285F4",
            "Unknown": "#9E9E9E",
        },
        template="plotly_dark",
    )
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=50, b=50))
    st.plotly_chart(fig, use_container_width=True)
