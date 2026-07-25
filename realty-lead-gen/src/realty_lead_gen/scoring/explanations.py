"""LLM-generated per-lead narratives ("why is this a good deal?").

Runs against the scored PropertyContext + ScoreOutput and produces a
short paragraph the frontend surfaces at the top of a lead card. Kept
separate from scoring so scoring stays deterministic + unit-testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from realty_lead_gen.agents.claude_client import ClaudeClient, text_of
from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger

if TYPE_CHECKING:
    from realty_lead_gen.scoring.base import PropertyContext, ScoreOutput

logger = get_logger(__name__)

_TEMPLATE = """You are helping a real estate {persona} evaluate a lead.

Property: {address}, {city}, {state} {postal_code}
Listed at ${list_price:,.0f}; AVM ${avm_value:,.0f} (confidence {avm_confidence});
ARV ${arv:,.0f}; rehab estimate ${rehab_low:,.0f} - ${rehab_high:,.0f}.
Overall condition: {condition}. Red flags: {red_flags}.
Days on market: {dom}. Motivation signals: {signals}.

Score: {score:.2f} (component breakdown: {components}).
Rationale: {rationale}

Write 2-3 sentences explaining why this property was surfaced, for the
{persona}. Be concrete: cite the specific numbers, no filler. Do not
invent facts."""


class NarrativeGenerator:
    def __init__(
        self,
        claude: ClaudeClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._claude = claude or ClaudeClient(self._settings)

    async def explain(
        self,
        *,
        persona: str,
        ctx: PropertyContext,
        result: ScoreOutput,
    ) -> str | None:
        if not self._claude.available:
            return result.rationale
        try:
            resp, _ = await self._claude.messages_create(
                model=self._settings.anthropic_model_reasoning,
                messages=[{"role": "user", "content": self._prompt(persona, ctx, result)}],
                max_tokens=512,
            )
            return text_of(resp).strip()
        except Exception:
            logger.exception("narrative.failed")
            return result.rationale

    def _prompt(self, persona: str, ctx: PropertyContext, result: ScoreOutput) -> str:
        p = ctx.property
        return _TEMPLATE.format(
            persona=persona,
            address=f"{p.street_number or ''} {p.street_name} {p.unit or ''}".strip(),
            city=p.city,
            state=p.state,
            postal_code=p.postal_code,
            list_price=(ctx.list_price_cents or 0) / 100,
            avm_value=(ctx.avm_value_cents or 0) / 100,
            avm_confidence=float(ctx.avm_confidence or 0),
            arv=(ctx.arv_cents or 0) / 100,
            rehab_low=(ctx.rehab_low_cents or 0) / 100,
            rehab_high=(ctx.rehab_high_cents or 0) / 100,
            condition=ctx.overall_condition or "unknown",
            red_flags=", ".join(ctx.red_flags) or "none",
            dom=ctx.days_on_market or 0,
            signals=", ".join(s.kind.value for s in ctx.signals) or "none",
            score=float(result.score),
            components=result.components,
            rationale=result.rationale or "",
        )
