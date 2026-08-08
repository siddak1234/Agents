# Findings in the required shape

Loaded on demand from [`SKILL.md`](SKILL.md) step 6. Every example below is
taken from a review that actually ran in this repository — none is invented,
because a fabricated example teaches a shape that has never survived contact
with a contributor.

Each finding carries **file:line · evidence · fix**, where evidence is a
command's output or a reproduced value, never a description. The set is chosen
to be diverse on purpose: two blockers that reproduce differently, one finding
correctly *dropped*, one Should fix, and one that was ours rather than the
contributor's.

<examples>

<example>
**Blocker — a reproduced wrong output.** The evidence is the value the agent
actually returned, next to what it should have returned.

> **3. Every billable call reports zero usage — `agent_main.py:102`, `planner.py:46-60`**
>
> `ok(capability, result)` never passes `usage`, and `response.usage` is never
> read. Reproduced with the client mocked to return 842 input / 213 output
> tokens:
>
> ```
> {"ok": true, "usage": {"input_tokens": 0, "output_tokens": 0, "model": null}}
> ```
>
> The brief's Part 2 Q7 requires usage reported for a model-calling agent. Zero
> is for when nothing was spent; here every caller sees $0 forever, on the
> agent's only paid capability.
>
> → Read `response.usage.input_tokens`/`output_tokens` and report `model`,
> and pass `usage=` into `ok()`, as `realty-lead-gen` wires `resp.usage` into
> its envelope.
</example>

<example>
**Blocker — an ordinary input that breaks it.** The evidence is the input, so
the contributor can paste it back and watch it fail.

> **1. Substring matching in the technique regexes — `phishing_triage.py:69`**
>
> `(irs|fbi|police|government|tax authority|court)` matches inside ordinary
> words:
>
> ```
> subject "First quarter numbers"          → techniques: ['authority_impersonation']   (irs ⊂ first)
> body "As a courtesy, here is the deck"   → techniques: ['authority_impersonation']   (court ⊂ courtesy)
> ```
>
> → Wrap each alternation in `\b(...)\b`. Do it per term rather than blanket —
> `bank` inside `banking` is a match worth keeping.
</example>

<example>
**Dropped, not downgraded — the scaffold does the same thing.** This one never
reaches the review. It is here because recognising it is the check: a
contributor who copied what we handed them has done nothing wrong.

> A reviewer reports that a capability's `input_schema` sets
> `additionalProperties: false` while nothing enforces it, and that no
> capability uses `enum` to constrain a string field.
>
> `agents/_template/agent.yaml` declares `additionalProperties: false` on both
> its capabilities and enforces it nowhere; no agent in the repository uses
> `enum`. Both are the house pattern. **Dropped** — neither appears in the
> review at any tier.
</example>

<example>
**Should fix — the scaffold marked it for replacement.** The mirror image of
the one above, and the distinction that separates them is whether the template
told the contributor to change the line.

> **Placeholder lint — `agent.yaml:19`**
>
> `compileall` only proves the code parses; the brief's own manifest example
> marks it `# replace with real linting`, and the agent that merged before
> yours made the same switch to a ruff config it owns during review.
>
> → Declare a real linter your folder owns.
</example>

<example>
**Ours, not the contributor's.** Findings that trace back to our platform or
our documentation belong in the review as context, never as a request. Say
what is true, say it is not theirs to fix, and take the follow-up.

> CI never injects `ANTHROPIC_API_KEY` into agent tests — confirmed: the
> workflow that runs `agents test` contains no reference to it. So the inert
> mock above costs nothing in CI; it costs money only on a machine where a real
> key is exported. Nothing for you to change here.
>
> *(Maintainer follow-up: the brief's "What good looks like" shows only
> subprocess tests, and the template docstring argues against testing
> `dispatch()` directly — which is the pattern that would have made the mock
> work. That is our gap, and it is why this is stated rather than asked.)*
</example>

</examples>
