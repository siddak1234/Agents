"""Golden schema check for the photo-grader prompt.

Ensures the tool schema stays stable across refactors; changes are
opt-in via updating the golden file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from realty_lead_gen.agents.photo_grader import photo_grader_tool_schema, prompt_version

GOLDEN = Path(__file__).parent.parent / "golden" / "photo_grader_tool_schema.json"


@pytest.mark.unit
class TestPhotoGraderPrompt:
    def test_prompt_version_pinned(self) -> None:
        assert prompt_version() == "photo_grader_v1"

    def test_tool_schema_matches_golden(self) -> None:
        current = json.loads(photo_grader_tool_schema())
        if not GOLDEN.exists():
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True))
            pytest.skip("wrote initial golden; re-run to compare")
        expected = json.loads(GOLDEN.read_text())
        assert current == expected, (
            "Photo grader schema drift. If intentional, delete tests/golden/"
            "photo_grader_tool_schema.json and re-run to regenerate."
        )
