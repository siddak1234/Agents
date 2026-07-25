"""Measure the live surface of the service. Writes a JSON report to a file.

This exists so ARCHITECTURE.md's "what's stubbed vs live" section is derived
from the code that ships rather than from memory. Everything it reports is
read off a real object: the ORM metadata, the OpenAPI document FastAPI
actually generates, arq's `WorkerSettings` class attributes, and each
adapter's own `available` property evaluated against a credential-free
`Settings`.

    python scripts/measure_surface.py            # -> surface.json
    python scripts/measure_surface.py out.json   # -> out.json

The report goes to a **file**, not to stdout, and that is deliberate rather
than clumsy. Building the app configures structlog, whose handler binds the
real `sys.stdout` at configuration time — `contextlib.redirect_stdout` does
not move it (verified), so any log line emitted during measurement would
interleave with the JSON and make `| jq` fail on a document that is
otherwise perfectly valid. Writing to a named file sidesteps the race
entirely and has the side benefit that two runs can be diffed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import realty_lead_gen.models  # noqa: F401  (populates `Base.metadata`)
from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.main import create_app
from realty_lead_gen.models import base as models_base
from realty_lead_gen.sources import registry
from realty_lead_gen.worker import WorkerSettings

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _schedule(field: set[int] | int | None) -> list[int] | None:
    """Normalize one arq cron field to a sorted list.

    `arq.cron.cron` accepts a bare `int`, a `set[int]`, or `None` for each
    of hour/minute/second and stores whichever it was given, so this is
    three shapes rather than one. `None` genuinely means "every value" and
    stays `None` — collapsing it to `[]` would read as "never".
    """
    if field is None:
        return None
    if isinstance(field, int):
        return [field]
    return sorted(field)


def _tables() -> dict[str, Any]:
    names = sorted(models_base.Base.metadata.tables)
    return {"count": len(names), "names": names}


def _openapi(settings: Settings) -> dict[str, Any]:
    app = create_app(settings)
    doc = app.openapi()
    operations: list[str] = [
        f"{method.upper()} {path}"
        for path, item in sorted(doc["paths"].items())
        for method in sorted(item)
        if method.lower() in _HTTP_METHODS
    ]
    schemas = sorted(doc.get("components", {}).get("schemas", {}))
    return {
        "path_count": len(doc["paths"]),
        "operation_count": len(operations),
        "operations": operations,
        "schema_count": len(schemas),
        "schemas": schemas,
        "has_jsondict_schema": "JSONDict" in schemas,
    }


def _worker() -> dict[str, Any]:
    return {
        # `getattr` rather than `.__name__`: arq types the entries as
        # `WorkerCoroutine`, a Protocol that does not declare `__name__`,
        # even though every registered value is a plain async function.
        "functions": [getattr(f, "__name__", repr(f)) for f in WorkerSettings.functions],
        "cron_jobs": [
            {
                "name": job.name,
                "hour": _schedule(job.hour),
                "minute": _schedule(job.minute),
                "second": _schedule(job.second),
                "max_tries": job.max_tries,
                "timeout": job.timeout_s,
                "unique": job.unique,
            }
            for job in WorkerSettings.cron_jobs
        ],
        "max_jobs": WorkerSettings.max_jobs,
        "job_timeout": WorkerSettings.job_timeout,
    }


def _adapters(bare: Settings) -> dict[str, Any]:
    every = registry.all_adapters(bare)
    return {
        "preference_order": [a.name for a in every],
        "available_without_credentials": [a.name for a in registry.all_available_adapters(bare)],
        "per_adapter": {
            adapter.name: {
                "class": type(adapter).__name__,
                "available": bool(adapter.available),
                "module": type(adapter).__module__,
            }
            for adapter in every
        },
    }


def main(argv: list[str]) -> int:
    get_settings.cache_clear()
    settings = get_settings()
    report = {
        "tables": _tables(),
        "openapi": _openapi(settings),
        "worker": _worker(),
        "adapters": _adapters(settings),
        "settings_env": settings.app_env,
    }
    destination = Path(argv[1]) if len(argv) > 1 else Path("surface.json")
    destination.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
