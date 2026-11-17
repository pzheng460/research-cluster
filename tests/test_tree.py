"""Trees describe persisted work; real CPU jobs verify parallel execution."""
import threading

import pytest

from conftest import register, submit
from test_execution import runtime, wait_for
from test_literature import create_and_plan, offline_literature
from research_cluster.config import Settings
from research_cluster.models import Study, Worker, now
from research_cluster.research import run_local_orchestrator
from research_cluster.schemas import JobInput
from research_cluster.service import create_job


def assert_tree(tree):
    nodes = {node["id"]: node for node in tree["nodes"]}
    assert len(nodes) == len(tree["nodes"])
    assert nodes[tree["root_id"]]["parent_id"] is None
    assert len(tree["edges"]) == len(nodes) - 1
    for node in nodes.values():
        seen, current = set(), node
        while current["parent_id"]:
            assert current["id"] not in seen
            seen.add(current["id"])
            current = nodes[current["parent_id"]]
        assert current["id"] == tree["root_id"]
    for edge in tree["edges"]:
        assert edge["source"] in nodes and edge["target"] in nodes
        assert nodes[edge["target"]]["parent_id"] == edge["source"]
    return nodes


def test_literature_tree_distinguishes_roles_from_running_models(client, app, offline_literature):
    study = create_and_plan(client, app)
    register(client)
    tree = client.get(f"/api/studies/{study['id']}/tree").json()
    nodes = assert_tree(tree)
    assert tree["summary"] == {"studies": 1, "proposals": 3, "papers": 2, "candidates": 0,
                                "running_role_steps": 0, "running_model_steps": 0, "running_experiments": 0}
    assert tree["runtime"]["model_ready"] is False
    assert tree["runtime"]["cpu_workers"] == 1 and tree["runtime"]["gpu_workers"] == 0
    assert nodes[tree["root_id"]]["status"] == "awaiting_review"
    assert nodes["synthesis:" + study["id"]]["details"]["method"] == "evidence_checklist"
    assert nodes["synthesis:" + study["id"]]["details"]["model_backed"] is False
    assert all(node["status"] == "recorded" and node["details"]["read_depth"] == "abstract"
               for node in tree["nodes"] if node["kind"] == "paper")
    task = study["config"]["literature"]["tasks"][0]
    client.post(f"/api/studies/{study['id']}/proposals/{task['id']}/accept")
    updated = client.get(f"/api/studies/{study['id']}/tree").json()
    assert next(n for n in updated["nodes"] if n["id"] == f"proposal:{study['id']}:{task['id']}")["status"] == "accepted"
    assert updated["summary"]["candidates"] == 0
    assert client.get("/api/studies/unknown/tree").status_code == 404


def test_child_execution_lineage_retries_results_and_expired_leases(client, app, offline_literature):
    root = create_and_plan(client, app)
    task = root["config"]["literature"]["tasks"][0]
    child = client.post("/api/studies", json={"name": "branch", "objective": "Test execution plumbing", "mode": "benchmark"}).json()
    worker_id, _ = register(client)
    submit(client, name="unrelated")
    with app.state.db.transaction() as session:
        study = session.get(Study, child["id"])
        study.config = {**study.config, "parent_research_id": root["id"], "proposal_id": task["id"]}
        first = create_job(session, JobInput(name="first", files={"main.py": "PRIVATE_SOURCE_SENTINEL"}), study=study, round_number=1)
        first.status, first.metrics, first.logs = "succeeded", {"score": 0.7}, "PRIVATE_LOG_SENTINEL"
        first.worker_id, first.lease_token = worker_id, "PRIVATE_LEASE_SENTINEL"
        first.artifacts = [{"name": "metrics.json", "size": 14}]
        second = create_job(session, JobInput(name="second"), study=study, round_number=2, parent_id=first.id)
        second.status, second.metrics = "succeeded", {"score": 0.9}
        study.best_job_id, study.best_score = second.id, 0.9
        retry = create_job(session, JobInput(name="retry"), parent_id=second.id)
        retry.status, retry.worker_id, retry.lease_until = "running", worker_id, now() - 1
        study.planning_token, study.planning_until = "expired-planner", now() - 1
        session.get(Worker, worker_id).last_seen = now() - 100
        ids = first.id, second.id, retry.id
    response = client.get(f"/api/studies/{root['id']}/tree")
    assert response.status_code == 200
    tree = response.json()
    nodes = assert_tree(tree)
    assert nodes["study:" + child["id"]]["parent_id"] == f"proposal:{root['id']}:{task['id']}"
    assert nodes["candidate:" + ids[1]]["parent_id"] == "candidate:" + ids[0]
    assert nodes["candidate:" + ids[2]]["parent_id"] == "candidate:" + ids[1]
    assert nodes["candidate:" + ids[2]]["status"] == "stale"
    assert nodes["result:" + ids[1]]["details"]["best"] is True
    assert nodes["result:" + ids[1]]["details"]["metrics"] == {"score": 0.9}
    assert tree["summary"]["candidates"] == 3 and tree["summary"]["studies"] == 2
    assert tree["summary"]["running_model_steps"] == tree["summary"]["running_experiments"] == 0
    assert tree["runtime"]["cpu_workers"] == 0
    for secret in ("PRIVATE_SOURCE_SENTINEL", "PRIVATE_LOG_SENTINEL", "PRIVATE_LEASE_SENTINEL", "secret_hash"):
        assert secret not in response.text


def test_corrupted_lineage_is_reported_without_cycles_or_dangling_edges(client, app):
    root = client.post("/api/studies", json={"name": "cycle", "objective": "Cycle protection", "mode": "benchmark"}).json()
    child = client.post("/api/studies", json={"name": "child", "objective": "Cycle protection", "mode": "benchmark"}).json()
    with app.state.db.transaction() as session:
        study = session.get(Study, root["id"])
        study.config = {**study.config, "parent_research_id": child["id"]}
        branch = session.get(Study, child["id"])
        branch.config = {**branch.config, "parent_research_id": root["id"]}
        first = create_job(session, JobInput(name="A"), study=study, round_number=1)
        second = create_job(session, JobInput(name="B"), study=study, round_number=2, parent_id=first.id)
        first.parent_id = second.id
    tree = client.get(f"/api/studies/{root['id']}/tree").json()
    assert_tree(tree)
    assert len(tree["warnings"]) >= 2
    assert tree["summary"]["candidates"] == 2


@pytest.mark.parametrize("runtime", [{"cpus": 2, "memory_mb": 1024, "concurrency": 2}], indirect=True)
def test_two_real_cpu_branches_run_in_parallel_and_update_tree(runtime):
    client, db, settings = runtime
    root = client.post("/api/studies", json={"name": "parallel smoke", "objective": "Verify two CPU branches"}).json()
    tasks = [{"id": f"smoke-{i}", "title": f"CPU branch {i}", "status": "accepted"} for i in range(2)]
    children = [client.post("/api/studies", json={"name": f"branch {i}", "objective": "Explicit infrastructure smoke", "mode": "benchmark", "rounds": 1}).json() for i in range(2)]
    with db.transaction() as session:
        parent = session.get(Study, root["id"])
        parent.config = {**parent.config, "literature": {"phase": "awaiting_review", "tasks": tasks}}
        for i, child in enumerate(children):
            study = session.get(Study, child["id"])
            study.config = {**study.config, "parent_research_id": root["id"], "proposal_id": tasks[i]["id"]}
            code = "import json,time\nfrom pathlib import Path\ntime.sleep(4)\nPath('metrics.json').write_text(json.dumps({'score':%d}))\nprint('real parallel CPU branch')\n" % (i + 1)
            create_job(session, JobInput(name=f"parallel {i}", files={"main.py": code}), study=study, round_number=1)
    settings.max_concurrent_research = 2
    stop = threading.Event()
    thread = threading.Thread(target=run_local_orchestrator, args=(settings, stop), daemon=True)
    thread.start()
    endpoint = f"/api/studies/{root['id']}/tree"
    try:
        running = wait_for(lambda: (t if (t := client.get(endpoint).json())["summary"]["running_experiments"] == 2 else None))
        assert_tree(running)
        assert running["runtime"]["cpu_execution_slots"] == 2
        assert running["runtime"]["research_concurrency_per_orchestrator"] == 2
        assert running["summary"]["running_model_steps"] == 0
        result = wait_for(lambda: (t if (t := client.get(endpoint).json())["summary"]["running_experiments"] == 0
                                  and sum(n["status"] == "completed" for n in t["nodes"] if n["kind"] == "execution") == 2 else None))
        measured = [node for node in result["nodes"] if node["kind"] == "result"]
        assert sorted(node["details"]["metrics"]["score"] for node in measured) == [1, 2]
        assert all(node["status"] == "succeeded" for node in measured)
        assert all(node["details"]["best"] for node in measured)
        assert len(client.get("/api/state").json()["jobs"]) == 2
    finally:
        stop.set()
        thread.join(10)
        assert not thread.is_alive()


def test_research_concurrency_has_explicit_bounds(tmp_path):
    with pytest.raises(ValueError, match="between 1 and 32"):
        Settings(data_dir=tmp_path, max_concurrent_research=0)
    with pytest.raises(ValueError, match="between 1 and 32"):
        Settings(data_dir=tmp_path, max_concurrent_research=33)


def test_local_scheduler_is_fair_and_starts_independent_steps_concurrently(client, app, monkeypatch):
    from research_cluster import research
    identities = {client.post("/api/studies", json={"name": f"waiting {i}", "objective": "Scheduling smoke"}).json()["id"]
                  for i in range(5)}
    settings = app.state.settings
    settings.max_concurrent_research = 2
    stop, release, parallel, all_seen = (threading.Event() for _ in range(4))
    lock, seen, running = threading.Lock(), set(), set()

    def step(db, settings, identity):
        with lock:
            seen.add(identity)
            running.add(identity)
            if len(running) == 2:
                parallel.set()
            if seen == identities:
                all_seen.set()
        try:
            assert release.wait(8)
        finally:
            with lock:
                running.remove(identity)
        return False  # Simulates studies that remain active while waiting.

    monkeypatch.setattr(research, "research_step", step)
    thread = threading.Thread(target=run_local_orchestrator, args=(settings, stop), daemon=True)
    thread.start()
    try:
        assert parallel.wait(5), "Two independent research steps did not start concurrently"
        release.set()
        assert all_seen.wait(6), "Later research branches were starved by earlier active studies"
    finally:
        release.set()
        stop.set()
        thread.join(10)
        assert not thread.is_alive()
