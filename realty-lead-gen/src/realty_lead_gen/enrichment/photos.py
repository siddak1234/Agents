"""Photo enrichment — attaches PhotoAnalysis rows for a property.

Batches photos to respect the vision LLM's per-request image cap and
aggregates system-level findings into a whole-property view.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from realty_lead_gen.agents.photo_grader import PhotoGrader, prompt_version
from realty_lead_gen.logging import get_logger
from realty_lead_gen.utils.jsontypes import JSONDict

logger = get_logger(__name__)

# Vision models accept many images per call but recall degrades past ~10.
BATCH_SIZE = 8


@dataclass(frozen=True, slots=True)
class PhotoEnrichmentOutput:
    overall_condition: str
    overall_confidence: float
    rehab_low_cents: int
    rehab_high_cents: int
    red_flags: list[str]
    line_items: list[JSONDict]
    prompt_version: str
    model_id: str
    total_cost_usd_micros: int
    per_photo_analyses: list[JSONDict]


class PhotoEnrichmentStep:
    kind = "photo_grading"

    def __init__(self, grader: PhotoGrader) -> None:
        self._grader = grader

    async def run(self, photo_urls: list[str]) -> PhotoEnrichmentOutput:
        if not photo_urls:
            return PhotoEnrichmentOutput(
                overall_condition="NOT_VISIBLE",
                overall_confidence=0.0,
                rehab_low_cents=0,
                rehab_high_cents=0,
                red_flags=[],
                line_items=[],
                prompt_version=prompt_version(),
                model_id="none",
                total_cost_usd_micros=0,
                per_photo_analyses=[],
            )

        batches = [photo_urls[i : i + BATCH_SIZE] for i in range(0, len(photo_urls), BATCH_SIZE)]

        aggregated_line_items: list[JSONDict] = []
        aggregated_red_flags: set[str] = set()
        confidence_sum = 0.0
        # We use the worst-condition-observed as the whole-property view —
        # a C5 kitchen + C3 exterior yields a C5 overall.
        condition_ordering = ["C1", "C2", "C3", "C4", "C5", "C6"]
        worst_condition_idx = -1
        total_low_cents = 0
        total_high_cents = 0
        total_cost = 0
        per_photo_analyses: list[JSONDict] = []
        model_id = "unknown"

        for batch_idx, batch in enumerate(batches):
            try:
                result = await self._grader.grade(batch)
            except Exception:
                logger.exception("photo_grader.batch_failed", batch_idx=batch_idx)
                continue
            model_id = result.usage.model
            total_cost += result.usage.cost_usd_micros
            total_low_cents += result.rehab_total_low_cents
            total_high_cents += result.rehab_total_high_cents
            aggregated_red_flags.update(x for x in result.red_flags if x != "none")
            confidence_sum += result.overall_confidence

            if result.overall_condition in condition_ordering:
                idx = condition_ordering.index(result.overall_condition)
                worst_condition_idx = max(worst_condition_idx, idx)

            for system in result.systems:
                aggregated_line_items.extend(
                    {
                        **item,
                        "system": system["system"],
                        "batch_index": batch_idx,
                    }
                    for item in system.get("repair_items", [])
                )

            per_photo_analyses.append(
                {
                    "batch_index": batch_idx,
                    "photo_urls": batch,
                    "condition": result.overall_condition,
                    "confidence": result.overall_confidence,
                    "systems": result.systems,
                    "notes": result.notes_for_reviewer,
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                    "cost_usd_micros": result.usage.cost_usd_micros,
                }
            )

        overall_condition = (
            condition_ordering[worst_condition_idx]
            if worst_condition_idx >= 0
            else "NEEDS_HUMAN_REVIEW"
        )
        overall_confidence = confidence_sum / len(batches) if batches else 0.0

        return PhotoEnrichmentOutput(
            overall_condition=overall_condition,
            overall_confidence=overall_confidence,
            rehab_low_cents=total_low_cents,
            rehab_high_cents=total_high_cents,
            red_flags=sorted(aggregated_red_flags),
            line_items=aggregated_line_items,
            prompt_version=prompt_version(),
            model_id=model_id,
            total_cost_usd_micros=total_cost,
            per_photo_analyses=per_photo_analyses,
        )


def _now() -> datetime:
    return datetime.now(UTC)
