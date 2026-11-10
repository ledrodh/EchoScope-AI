"""Integration with Google GenAI SDK (gemini-3.8-flash) for structured strategic reporting."""

import json
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from echoscope.ai.prompts import PROMPT_VERSION, SYSTEM_INSTRUCTION, build_analysis_payload
from echoscope.ai.schemas import (
    EchoScopeReport,
    MetricReference,
    PerformancePattern,
    StrategicHypothesis,
    validate_report_references,
)
from echoscope.analytics.metrics import ChannelSummaryMetrics
from echoscope.config import get_settings


def create_fallback_report(
    channel_metrics: ChannelSummaryMetrics,
    reason: str,
    model_name: str = "deterministic_fallback",
) -> EchoScopeReport:
    """Generates a clean, factual fallback report using exclusively Python-calculated metrics.

    Ensures the user always receives actionable, verified data even during AI outages or validation failures.
    """
    obs_data = [
        MetricReference(
            metric_id="median_views",
            interpretation=(
                f"The channel typical performance is {channel_metrics.median_views:,.0f} views per video "
                f"(sample size: {channel_metrics.sample_size_videos} videos)."
            ),
        ),
        MetricReference(
            metric_id="average_views",
            interpretation=f"The arithmetic mean stands at {channel_metrics.average_views:,.0f} views per video.",
        ),
        MetricReference(
            metric_id="upload_frequency_per_week",
            interpretation=(
                f"Publication cadence is approximately {channel_metrics.upload_frequency_per_week:.1f} videos per week."
            ),
        ),
    ]

    if channel_metrics.views_to_subscribers_ratio is not None:
        obs_data.append(
            MetricReference(
                metric_id="views_to_subscribers_ratio",
                interpretation=(
                    f"Views-to-Subscriber Ratio is {channel_metrics.views_to_subscribers_ratio:.2f}."
                ),
            )
        )

    patterns = [
        PerformancePattern(
            pattern_title="Consistency Dispersion",
            supporting_metric_ids=["p25_views", "p75_views"],
            observation=(
                f"50% of the channel's videos fall within the interquartile range of "
                f"{channel_metrics.p25_views:,.0f} (P25) to {channel_metrics.p75_views:,.0f} (P75) views."
            ),
        )
    ]

    hypotheses = [
        StrategicHypothesis(
            statement="Maintain consistent cadence and monitor formats with higher view retention.",
            supporting_metrics=["median_views", "upload_frequency_per_week"],
            limitations=["Generated via deterministic fallback due to external AI unavailability."],
            confidence_level="medium",
        )
    ]

    limitations = [
        f"Automated fallback engaged: {reason}",
        "No private retention, CTR, or impression data available via public YouTube Data API.",
        f"Analysis bounded to sample of {channel_metrics.sample_size_videos} videos.",
    ]

    return EchoScopeReport(
        channel_handle=channel_metrics.handle,
        prompt_version=PROMPT_VERSION,
        model_used=model_name,
        observed_data=obs_data,
        performance_patterns=patterns,
        strategic_hypotheses=hypotheses,
        study_limitations=limitations,
    )


class GeminiAnalysisClient:
    """Manages structured AI generation with schema validation and deterministic fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any = None,
    ):
        settings = get_settings()
        self.api_key: str | None = api_key or settings.gemini_api_key
        self.model_name = model or settings.gemini_model
        if client is not None:
            self._client = client
        elif self.api_key:
            self._client = genai.Client(api_key=self.api_key)
        else:
            self._client = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY is not configured in environment or settings.")
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def generate_report(
        self,
        channel_metrics: ChannelSummaryMetrics,
        top_videos: list[dict[str, Any]],
    ) -> EchoScopeReport:
        """Generates a structured, evidence-backed strategic report.

        Enforces Pydantic schema decoding and validates all metric_id references.
        Falls back seamlessly to deterministic metrics if the API call fails or hallucinated IDs appear.
        """
        valid_metrics = channel_metrics.to_analytical_payload()
        prompt_content = build_analysis_payload(channel_metrics, top_videos)

        try:
            client = self._ensure_client()
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=EchoScopeReport,
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2,  # Low temperature for strict analytical rigor
            )

            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt_content,
                config=config,
            )

            raw_text = response.text or ""
            if not raw_text.strip():
                return create_fallback_report(
                    channel_metrics, "Empty response from Gemini API", self.model_name
                )

            data = json.loads(raw_text)
            report = EchoScopeReport.model_validate(data)

            # Validate that AI references only verified Python metric identifiers
            is_valid, violations = validate_report_references(report, valid_metrics)
            if not is_valid:
                violation_summary = "; ".join(violations)
                return create_fallback_report(
                    channel_metrics,
                    f"Report contained unauthorized metric references ({violation_summary})",
                    "deterministic_fallback",
                )

            return report

        except APIError as e:
            return create_fallback_report(
                channel_metrics, f"Gemini API Error: {str(e)}", "deterministic_fallback"
            )
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return create_fallback_report(
                channel_metrics, f"Schema validation failure: {e}", "deterministic_fallback"
            )
        except Exception as e:
            return create_fallback_report(
                channel_metrics, f"Unexpected AI generation failure: {e}", "deterministic_fallback"
            )
