"""realty-lead-gen — agentic real-estate lead generation backend.

Package layout:
    config       - typed application settings (pydantic-settings)
    db           - async SQLAlchemy engine + session helpers
    logging      - structlog + OpenTelemetry setup
    models       - SQLAlchemy ORM (write side)
    schemas      - Pydantic v2 DTOs (API + inter-layer)
    sources      - ingestion adapters (MLS, portals, off-market)
    enrichment   - photo grading, AVM, comps, skip trace, signals
    scoring      - per-persona scoring models (flipper, wholesaler, agent)
    matching     - buyer <-> property matching
    agents       - Claude Agent SDK usages (reasoning + vision)
    pipeline     - normalize / dedup / orchestrate the DAG
    api          - FastAPI routers + dependencies + auth + middleware
    jobs         - arq job definitions
    utils        - address hashing, geo, money, retry helpers

The public entrypoints are `main:app` (FastAPI) and `worker:WorkerSettings` (arq).
"""

from __future__ import annotations

__version__ = "0.1.0"
