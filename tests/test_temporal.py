"""Opt-in integration with a real, temporary Temporal development service."""
import asyncio
import os
import uuid

import pytest

from test_execution import runtime

pytestmark = pytest.mark.skipif(os.getenv("CLUSTER_TEST_TEMPORAL") != "1", reason="Set CLUSTER_TEST_TEMPORAL=1 to start Temporal")


def test_temporal_drives_real_research_and_cpu_execution(runtime):
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from research_cluster.temporal_engine import ResearchActivities, ResearchWorkflow
    client, db, settings = runtime
    study = client.post("/api/studies", json={"name": "temporal-research", "objective": "convergence", "mode": "benchmark", "rounds": 2}).json()

    async def run():
        async with await WorkflowEnvironment.start_local(dev_server_log_level="error") as env:
            activities = ResearchActivities(db, settings)
            async with Worker(env.client, task_queue="research-test", workflows=[ResearchWorkflow], activities=[activities.step]):
                await asyncio.wait_for(env.client.execute_workflow(ResearchWorkflow.run, study["id"],
                    id="test-" + uuid.uuid4().hex, task_queue="research-test"), timeout=45)

    asyncio.run(run())
    result = client.get("/api/state").json()
    assert result["studies"][0]["status"] == "completed"
    assert len(result["jobs"]) == 2
    assert all(j["status"] == "succeeded" for j in result["jobs"])


def test_temporal_defaults_to_papers_and_waits_for_task_review(runtime, monkeypatch):
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from research_cluster.temporal_engine import ResearchActivities, ResearchWorkflow
    from research_cluster import literature
    from test_literature import fake_collection
    client, db, settings = runtime
    monkeypatch.setattr(literature, 'collect', fake_collection)
    monkeypatch.setattr(literature, 'read_excerpt', lambda _: (_ for _ in ()).throw(ValueError('No HTML in fixture')))
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.delenv('LLM_MODEL', raising=False)
    study=client.post('/api/studies',json={'name':'Temporal literature','objective':'Investigate DSpark architecture'}).json()

    async def run():
        async with await WorkflowEnvironment.start_local(dev_server_log_level='error') as env:
            activities=ResearchActivities(db,settings)
            async with Worker(env.client,task_queue='literature-test',workflows=[ResearchWorkflow],activities=[activities.step]):
                handle=await env.client.start_workflow(ResearchWorkflow.run,study['id'],id='literature-'+uuid.uuid4().hex,task_queue='literature-test')
                for _ in range(80):
                    current=client.get('/api/studies/'+study['id']).json()
                    if current['config']['literature']['phase']=='awaiting_review':
                        break
                    await asyncio.sleep(.15)
                else:
                    raise AssertionError('Temporal did not finish literature intake')
                assert len(current['config']['literature']['tasks']) == 3
                assert client.get('/api/state').json()['jobs'] == []
                client.post('/api/studies/'+study['id']+'/cancel')
                await asyncio.wait_for(handle.result(),timeout=10)

    asyncio.run(run())
