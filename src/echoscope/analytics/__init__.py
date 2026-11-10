"""Analytics package for EchoScope AI."""

from echoscope.analytics.format_classifier import (
    FormatClassificationResult,
    classify_video_format,
)
from echoscope.analytics.growth import (
    ChannelGrowthComparison,
    VideoGrowthItem,
    compute_channel_growth,
)
from echoscope.analytics.metrics import (
    ChannelSummaryMetrics,
    FormatBreakdown,
    calculate_channel_metrics,
    compute_sample_market_share,
)

__all__ = [
    "ChannelGrowthComparison",
    "ChannelSummaryMetrics",
    "FormatBreakdown",
    "FormatClassificationResult",
    "VideoGrowthItem",
    "calculate_channel_metrics",
    "classify_video_format",
    "compute_channel_growth",
    "compute_sample_market_share",
]
