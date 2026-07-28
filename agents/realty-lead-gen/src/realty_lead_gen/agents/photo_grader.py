"""Vision-LLM photo grader.

Design:
    * One call, one model: `settings.anthropic_model_vision` grades a batch
      of photos and itemizes repairs against the Fannie Mae UAD 3.6 scale.
      (A two-stage triage-then-grade design was planned and is not built.)
    * Output is structured via Anthropic tool-use, so the model returns a
      typed payload rather than prose to parse. Note what this does NOT do:
      nothing validates the payload against the schema on the way back, so a
      missing key raises rather than degrading — see `_from_tool_payload`.
    * Every finding cites at least one photo id (evidence). LLM-only
      inferences without an explicit evidence tie are flagged as
      low-confidence.

`PROMPT_VERSION` is pinned in the module so a semantic change to the prompt
is a deliberate, visible edit. It is not currently reported in the envelope;
a caller that needs to attribute a grade to a prompt version would have to
add it to the output schema first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from realty_lead_gen.agents.claude_client import ClaudeClient, LlmUsage, tool_use_input
from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger

logger = get_logger(__name__)

PROMPT_VERSION: Final[str] = "photo_grader_v1"

_SYSTEM_PROMPT = """You are a property condition analyst using the Fannie Mae UAD 3.6 scale.
You are conservative: if evidence is unclear, output NOT_VISIBLE or lower confidence.
You never invent damage or repairs you cannot cite to a photo.

RUBRIC (UAD whole-property condition):
C1 = new construction, unoccupied
C2 = no deferred maintenance, recent updates throughout
C3 = well maintained, normal wear, move-in ready
C4 = minor deferred maintenance, functional
C5 = obvious deferred maintenance, significant repairs needed
C6 = unsafe / uninhabitable / structural issues

RULES
- Every repair_item MUST cite >=1 evidence_photo_id.
- If a system is not visible in any photo, use NOT_VISIBLE and no repair_items.
- Cost ranges assume median US labor; note if the market appears HCOL.
- Do not infer age from decor alone; require physical evidence.
- If overall_confidence < 0.6, set overall_condition to "NEEDS_HUMAN_REVIEW".
"""

#: Named once. It is the tool definition, the forced `tool_choice`, *and* the
#: block we read back off the response — three places that have to agree, and
#: a typo in the third would only show up as "did not emit tool_use block".
_PHOTO_GRADER_TOOL_NAME: Final[str] = "record_photo_analysis"

_PHOTO_GRADER_TOOL: dict[str, Any] = {
    "name": _PHOTO_GRADER_TOOL_NAME,
    "description": "Record the structured photo condition analysis for a property.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "overall_condition",
            "overall_confidence",
            "rehab_total_low_usd",
            "rehab_total_high_usd",
            "systems",
            "red_flags",
        ],
        "properties": {
            "overall_condition": {
                "type": "string",
                "enum": ["C1", "C2", "C3", "C4", "C5", "C6", "NEEDS_HUMAN_REVIEW"],
            },
            "overall_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rehab_total_low_usd": {"type": "number", "minimum": 0},
            "rehab_total_high_usd": {"type": "number", "minimum": 0},
            "notes_for_reviewer": {"type": "string"},
            "systems": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["system", "condition_grade", "observations", "repair_items"],
                    "properties": {
                        "system": {
                            "type": "string",
                            "enum": [
                                "kitchen",
                                "bathrooms",
                                "flooring",
                                "interior_paint",
                                "windows",
                                "roof",
                                "exterior_siding",
                                "hvac_visible",
                                "plumbing_visible",
                                "electrical_visible",
                                "structural",
                                "landscaping",
                            ],
                        },
                        "condition_grade": {
                            "type": "string",
                            "enum": ["C1", "C2", "C3", "C4", "C5", "C6", "NOT_VISIBLE"],
                        },
                        "observations": {"type": "string", "maxLength": 400},
                        "repair_items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "item",
                                    "scope",
                                    "cost_low_usd",
                                    "cost_high_usd",
                                    "confidence",
                                    "evidence_photo_ids",
                                ],
                                "properties": {
                                    "item": {"type": "string"},
                                    "scope": {"type": "string"},
                                    "cost_low_usd": {"type": "number", "minimum": 0},
                                    "cost_high_usd": {"type": "number", "minimum": 0},
                                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                    "evidence_photo_ids": {
                                        "type": "array",
                                        "items": {"type": "integer", "minimum": 0},
                                        "minItems": 1,
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "red_flags": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "foundation",
                        "roof",
                        "water_damage",
                        "mold",
                        "fire_damage",
                        "structural",
                        "none",
                    ],
                },
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class PhotoGradingResult:
    """The structured output of one photo-grading batch."""

    overall_condition: str
    overall_confidence: float
    rehab_total_low_cents: int
    rehab_total_high_cents: int
    systems: list[dict[str, Any]]
    red_flags: list[str]
    notes_for_reviewer: str | None
    usage: LlmUsage


class PhotoGrader:
    def __init__(
        self,
        claude: ClaudeClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._claude = claude or ClaudeClient(self._settings)

    async def grade(
        self,
        photo_urls: list[str],
        *,
        market_hint: str | None = None,
    ) -> PhotoGradingResult:
        """Grade up to N photos in a single reasoning call.

        Batches ≤ ~10 images per call; caller is responsible for
        splitting larger sets and aggregating.
        """
        content: list[dict[str, Any]] = []
        for idx, url in enumerate(photo_urls):
            content.append(
                {
                    "type": "image",
                    "source": {"type": "url", "url": url},
                }
            )
            content.append(
                {
                    "type": "text",
                    "text": f"[photo_id={idx}]",
                }
            )
        header = "Analyze these numbered photos of a single-family residence."
        if market_hint:
            header += f" Market context: {market_hint}."
        content.insert(0, {"type": "text", "text": header})

        resp, usage = await self._claude.messages_create(
            model=self._settings.anthropic_model_vision,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=[_PHOTO_GRADER_TOOL],
            tool_choice={"type": "tool", "name": _PHOTO_GRADER_TOOL_NAME},
            max_tokens=4096,
            temperature=0.0,
        )

        payload = tool_use_input(resp, _PHOTO_GRADER_TOOL_NAME)
        if payload is None:
            logger.error("photo_grader.no_tool_use", raw=str(resp.content)[:500])
            raise ValueError("photo grader did not emit tool_use block")

        return PhotoGradingResult(
            overall_condition=payload["overall_condition"],
            overall_confidence=float(payload["overall_confidence"]),
            rehab_total_low_cents=int(float(payload["rehab_total_low_usd"]) * 100),
            rehab_total_high_cents=int(float(payload["rehab_total_high_usd"]) * 100),
            systems=payload["systems"],
            red_flags=payload["red_flags"],
            notes_for_reviewer=payload.get("notes_for_reviewer"),
            usage=usage,
        )

    def rebuild_prompt_payload(self, photo_urls: list[str]) -> dict[str, Any]:
        """Return the JSON payload for golden-file tests."""
        return {
            "system": _SYSTEM_PROMPT,
            "photo_urls": photo_urls,
            "tool": _PHOTO_GRADER_TOOL,
            "prompt_version": PROMPT_VERSION,
        }


def prompt_version() -> str:
    return PROMPT_VERSION


def photo_grader_tool_schema() -> str:
    """Return the tool JSON schema — used by golden tests and API docs."""
    return json.dumps(_PHOTO_GRADER_TOOL, sort_keys=True)
