"""Temporal owns research transitions; compute remains behind the job broker."""
import asyncio
from datetime import timedelta
import logging

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker as TemporalWorker


@workflow.defn
class ResearchWorkflow:
    @workflow.run
    async def run(self, study_id: str):
        for _ in range(200):
            done = await workflow.execute_activity("research_step", study_id,
                start_to_close_timeout=timedelta(hours=2), retry_policy=RetryPolicy(maximum_attempts=3))
            if done:
                return
            await workflow.sleep(2)
        workflow.continue_as_new(study_id)


class ResearchActivities:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    @activity.defn(name="research_step")
    async def step(self, study_id: str) -> bool:
        from .research import research_step
        return await asyncio.to_thread(research_step, self.db, self.settings, study_id)


async def run_temporal(settings):
    from sqlalchemy import select
    from .database import Database
    from .models import Study
    db = Database(settings.database_url)
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    activities = ResearchActivities(db, settings)

    async def dispatch():
        while True:
            try:
                with db.transaction(write=False) as s:
                    identities = list(s.scalars(select(Study.id).where(Study.status.in_(["active", "paused"]))))
                for identity in identities:
                    try:
                        await client.start_workflow(ResearchWorkflow.run, identity, id=f"research-{identity}",
                            task_queue="research-control", id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY)
                    except WorkflowAlreadyStartedError:
                        pass
            except Exception:
                logging.exception("Temporal dispatch failed; retrying")
            await asyncio.sleep(2)

    worker = TemporalWorker(client, task_queue="research-control", workflows=[ResearchWorkflow],
                            activities=[activities.step], max_concurrent_activities=settings.max_concurrent_research)
    try:
        await asyncio.gather(worker.run(), dispatch())
    finally:
        db.engine.dispose()
