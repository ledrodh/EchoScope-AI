"""Table presentation components with formatted columns and sorting."""

import pandas as pd
import streamlit as st


def render_channels_table(macro_df: pd.DataFrame) -> None:
    """Renders formatted table of tracked channels."""
    if macro_df.empty:
        st.info("No channels recorded in this execution run.")
        return

    display_df = macro_df.copy()
    columns_map = {
        "Channel": "Channel",
        "Subs": "Subscribers",
        "Videos_Published": "Videos (n)",
        "Total_Views": "Total Views",
        "Shorts_Views": "Shorts Views",
        "Long_Views": "Long-form Views",
        "Unknown_Views": "Unknown Views",
        "Total_Likes": "Total Likes",
    }
    cols = [c for c in columns_map.keys() if c in display_df.columns]
    display_df = pd.DataFrame(display_df[cols]).rename(columns=columns_map)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )


def render_videos_table(videos_df: pd.DataFrame) -> None:
    """Renders formatted video details table."""
    if videos_df.empty:
        st.info("No videos found.")
        return

    display_df = videos_df.copy()
    desired_cols = [
        "title",
        "category",
        "views",
        "likes",
        "date",
        "duration_seconds",
        "classification_evidence",
    ]
    cols = [c for c in desired_cols if c in display_df.columns]
    rename_cols = {
        "title": "Title",
        "category": "Format",
        "views": "Views",
        "likes": "Likes",
        "date": "Date",
        "duration_seconds": "Duration (s)",
        "classification_evidence": "Format Evidence",
    }
    display_df = pd.DataFrame(display_df[cols]).rename(columns=rename_cols)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )
