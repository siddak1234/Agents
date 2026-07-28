"""realty-lead-gen — photo-condition grading over agentcall/v1.

Package layout:
    agentcall  - the wire adapter: one JSON request in, one envelope out
    agents     - Claude usage (the vision grader and its client)
    config     - typed settings (pydantic-settings)
    logging    - structlog setup
    utils      - HTTP status triage, retry policy, JSON typing

The public entrypoint is `agentcall:main`, declared in `agent.yaml` and
invoked by the orchestrator as a subprocess.

The lead-generation service this grew out of — FastAPI app, Postgres models,
ingestion adapters, scoring, background jobs — lives in its own repository.
Its modules used to be listed here; they are not coming back (see CLAUDE.md).
"""

from __future__ import annotations

__version__ = "0.1.0"
