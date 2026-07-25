"""arq worker settings.

Run with:  arq realty_lead_gen.worker.WorkerSettings
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron

from realty_lead_gen.config import get_settings
from realty_lead_gen.jobs.daily_sweep import daily_sweep_job
from realty_lead_gen.jobs.enrich import enrich_property_job
from realty_lead_gen.jobs.ingest import ingest_region_job
from realty_lead_gen.jobs.outbox_relay import outbox_relay_job
from realty_lead_gen.jobs.score import score_property_job
from realty_lead_gen.logging import configure_logging

if TYPE_CHECKING:
    from arq.cron import CronJob
    from arq.typing import WorkerCoroutine

    from realty_lead_gen.utils.jsontypes import JSONDict


async def on_startup(ctx: JSONDict) -> None:
    settings = get_settings()
    configure_logging(settings)


class WorkerSettings:
    # `ClassVar` on both list members is not decoration: arq reads these off
    # the *class* (it never instantiates `WorkerSettings`), and a bare mutable
    # class attribute is the shared-state footgun RUF012 exists to catch —
    # every worker in the fleet would alias the same list object. Annotating
    # them states the intent that they are class-level configuration, and
    # makes an accidental `self.functions.append(...)` a type error.
    functions: ClassVar[list[WorkerCoroutine]] = [
        ingest_region_job,
        enrich_property_job,
        score_property_job,
        daily_sweep_job,
        outbox_relay_job,
    ]
    on_startup = on_startup
    max_jobs = 20
    job_timeout = 600
    keep_result = 3600
    cron_jobs: ClassVar[list[CronJob]] = [
        # 06:00 UTC daily — before US market open
        cron(daily_sweep_job, hour={6}, minute={0}),
        # Every minute at :00. arq's `unique=True` default means exactly one
        # worker in the fleet fires each tick; `SKIP LOCKED` inside the job
        # then makes it safe even if that guarantee ever slipped. A minute of
        # notification latency is well inside what a lead alert tolerates,
        # and the batch cap bounds the work each tick can do.
        cron(
            outbox_relay_job,
            second={0},
            max_tries=1,
            timeout=120,
        ),
    ]

    @classmethod
    def redis_settings(cls) -> RedisSettings:
        settings = get_settings()
        return RedisSettings.from_dsn(settings.redis_url)


def run() -> None:
    """Console entry point for `pyproject.toml`."""
    # `[sys.executable, "-m", "arq", ...]` rather than the bare name `"arq"`.
    # A partial path is resolved against whatever `PATH` the process inherited,
    # which in a container is attacker-influenceable and, more mundanely, is
    # how you end up running a *different* virtualenv's arq than the one this
    # package was installed into. `sys.executable` is the interpreter already
    # running us, so `-m arq` is guaranteed to be the arq we depend on.
    sys.exit(
        subprocess.call([sys.executable, "-m", "arq", "realty_lead_gen.worker.WorkerSettings"])
    )
