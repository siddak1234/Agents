# readability-grader

Scores a block of text for reading difficulty using the Flesch-Kincaid
Grade Level and Flesch Reading Ease formulas, and returns a plain-language
verdict. Called over `agentcall/v1`; see `docs/AGENT_PROTOCOL.md` at the
repository root.

Built for content pipelines, EdTech authoring tools, or healthcare
document checkers that need to verify text is written at an appropriate
reading level before it reaches students or patients — without every
caller having to know the underlying formulas.

## Quickstart

No install step — standard library only.

```bash
echo '{"protocol":"agentcall/v1","capability":"grade_text","input":{"text":"The cat sat on the mat."}}' \
  | python3 agent_main.py
```

Or through the orchestrator, from the repository root:

```bash
uv run agents describe readability-grader
uv run agents call readability-grader grade_text \
  --input '{"text": "Photosynthesis is the process by which plants convert light energy into chemical energy.", "target_grade": 8}'
```

## Capabilities

| Capability | In | Out |
|---|---|---|
| `describe` | `{}` | name, protocol, capability list |
| `grade_text` | `text` (non-empty string), optional `target_grade` (number) | word/sentence counts, avg sentence length, avg syllables per word, Flesch Reading Ease, Flesch-Kincaid grade level, plain verdict, `meets_target` |

Full schemas in [`agent.yaml`](./agent.yaml).

### `verdict` bands

Derived from Flesch Reading Ease, standard 6-band scale:

| Score | Verdict |
|---|---|
| 90–100 | very easy |
| 80–89 | easy |
| 70–79 | fairly easy |
| 60–69 | standard |
| 50–59 | fairly difficult |
| 30–49 | difficult |
| below 30 | very confusing |

## Configuration

None. No environment variables, no credentials, no network access —
`runtime.env.inherit` in `agent.yaml` is empty on purpose.

## What this will not do

- **No dictionary-based syllable accuracy.** Syllable counts come from a
  vowel-group heuristic (count vowel-sound groups, subtract one for a
  trailing silent "e"), not a pronunciation dictionary like CMUdict. It is
  correct on most everyday English words and wrong on some irregular ones
  (e.g. "queue", "onomatopoeia"). This is a deliberate trade-off to stay
  dependency-free — see `readability.py`.
- **No non-English support.** The formulas and heuristic are calibrated
  for English text only.
- **No `unavailable` failure mode.** This agent has no external
  dependency — nothing it calls can be down or missing configuration.
  Its only failure type is `invalid_request`.
- **No comprehension or meaning analysis.** It measures mechanical
  reading difficulty (sentence and word length), not whether the content
  is factually correct, well-organized, or appropriate in tone.

## Design notes

- **stdout carries the envelope and nothing else.** `agent_main.py`
  rebinds `sys.stdout` to stderr before doing anything else, and writes
  the envelope only through the real stdout it kept aside.
- **`agent_main.py` has no business logic.** All scoring lives in
  `readability.py`, which imports nothing about JSON or the protocol —
  it is plain functions over strings and numbers, testable on its own.
- **`describe` never depends on `grade_text` working.** Both are cheap
  here since there's no external call to gate, but the split is kept
  anyway for consistency with agents that do have one.
