"""Read-only publication of native annual scheduling research."""
from pathlib import Path

from server.analysis import _record

RESEARCH_ROOT = "amat/outputs/analysis/2026-10-20-native-annual-scheduling"


def published_comparison(root: Path):
    path = root / RESEARCH_ROOT / "publication.json"
    if not path.is_file():
        return None
    try:
        value = _record(path)
        if value.get("schema") != "native-annual-dashboard/1":
            raise ValueError("Unrecognized annual research publication")
        if value.get("execution_authority") != "none":
            raise ValueError("Research publication cannot authorize execution")
        summary = root / RESEARCH_ROOT / "benchmark-summary.json"
        if summary.is_file():
            try:
                review = _record(summary)
                if review.get("schema") != "native-annual-benchmark-summary/1" or review.get("execution_authority") != "none":
                    raise ValueError("Unrecognized benchmark summary")
                value = {**value, "benchmark_details": review, "benchmark_review": review["message"]}
            except (OSError, ValueError, KeyError):
                value = {**value, "benchmark_details": {"status": "unavailable", "message": "The latest benchmark review needs verification."}}
        for field, folder, label in [
            ("quality_comparison", "2026-10-20-quality-annual-allocation", "Quality annual"),
            ("cost_aware_comparison", "2026-10-20-cost-aware-annual-allocation", "Cost-aware annual"),
        ]:
            followup_path = root / "amat/outputs/analysis" / folder / "publication.json"
            if followup_path.is_file():
                try:
                    followup = _record(followup_path)
                    if followup.get("schema") != "native-annual-dashboard/1" or followup.get("execution_authority") != "none":
                        raise ValueError("Invalid annual publication")
                    value = {**value, field: followup}
                except (OSError, ValueError, KeyError):
                    value = {**value, field: {"status": "unavailable", "message": label + " research evidence needs verification."}}
        return value
    except (OSError, ValueError, KeyError):
        return {"status": "unavailable", "message": "Annual research evidence needs verification."}
