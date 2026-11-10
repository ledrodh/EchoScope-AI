"""AI strategic analysis package for EchoScope AI."""

from echoscope.ai.gemini_client import GeminiAnalysisClient
from echoscope.ai.prompts import PROMPT_VERSION, SYSTEM_INSTRUCTION
from echoscope.ai.schemas import (
    EchoScopeReport,
    MetricReference,
    PerformancePattern,
    StrategicHypothesis,
    validate_report_references,
)

__all__ = [
    "EchoScopeReport",
    "GeminiAnalysisClient",
    "MetricReference",
    "PROMPT_VERSION",
    "PerformancePattern",
    "SYSTEM_INSTRUCTION",
    "StrategicHypothesis",
    "validate_report_references",
]
