import json
import os
from pathlib import Path
import threading

import pytest

from conftest import claim, finish, register
from test_execution import runtime, wait_for
from test_literature import create_and_plan, offline_literature
from research_cluster.lean_runner import verify
from research_cluster.models import Job, Study, Verification
from research_cluster.research import research_step, run_local_orchestrator
from research_cluster.verification import sha, smoke_policy, verification_step
from research_cluster.worker import ExecutionWorker


@pytest.fixture
def lean_home():
    if os.getenv("CLUSTER_TEST_LEAN") != "1":
        pytest.skip("Set CLUSTER_TEST_LEAN=1 for real pinned Lean checks")
    home = Path(os.getenv("CLUSTER_LEAN_HOME", Path(__file__).parents[1] / ".state/toolchains/lean-4.35.0-linux")).resolve()
    assert (home / "bin/leanchecker").is_file(), "Install the pinned toolchain before running Lean integration checks"
    return home


def inputs(tmp_path, proofs, spec=None):
    policy = smoke_policy()
    if spec:
        policy["spec_source"] = spec
    raw = json.dumps(proofs)
    metadata = {"policy": policy, "candidate_hash": "a" * 64, "spec_hash": sha(policy["spec_source"]), "proof_hash": sha(raw)}
    (tmp_path / "verification-input.json").write_text(json.dumps(metadata))
    (tmp_path / "ClusterSpec.lean").write_text(policy["spec_source"])
    (tmp_path / "proofs.json").write_text(raw)
    return metadata


def prepared(client, app):
    study = client.post("/api/verification/smoke").json()
    worker = register(client)
    research_step(app.state.db, app.state.settings, study["id"])
    candidate = claim(client, worker)
    assert finish(client, candidate, metrics={"score": 99}).status_code == 200
    research_step(app.state.db, app.state.settings, study["id"])
    check = client.get(f"/api/jobs/{candidate['id']}/verification").json()["verification"]
    return study, candidate, check


def test_required_gate_rejects_missing_or_forged_report_and_keeps_cpu_pools_independent(client, app):
    study, candidate, check = prepared(client, app)
    current = client.get(f"/api/studies/{study['id']}").json()
    assert current["best_job_id"] is None and current["status"] == "active"
    assert check["policy"]["mode"] == "required"
    assert claim(client, register(client, id="other-cpu-worker")) is None
    verifier = register(client, id="lean-worker", pool_id="lean-cpu")
    job = claim(client, verifier)
    assert job["kind"] == "cpu" and job["pool_id"] == "lean-cpu"
    assert job["study_id"] is None
    # A success exit and a numeric metric alone are never a proof certificate.
    assert finish(client, job, metrics={"verification_passed": 1}).json()["status"] == "failed"
    research_step(app.state.db, app.state.settings, study["id"])
    current = client.get(f"/api/studies/{study['id']}").json()
    assert current["best_job_id"] is None and current["status"] == "failed"
    tree = client.get(f"/api/studies/{study['id']}/tree").json()
    assert tree["summary"]["candidates"] == 1
    assert len([node for node in tree["nodes"] if node["kind"] == "verification"]) == 1
    assert not any(node["status"] == "passed" for node in tree["nodes"])


@pytest.mark.parametrize("mutation", ["wrong_hash", "missing_target", "custom_axiom", "no_kernel_replay", "malformed_targets"])
def test_incomplete_certificates_cannot_promote_candidates(client, app, mutation):
    study, candidate, check = prepared(client, app)
    verifier = register(client, id="lean-worker", pool_id="lean-cpu")
    job = claim(client, verifier)
    metadata = job["spec"]["verification"]
    report = {"status": "passed", "toolchain": "4.35.0", "kernel_rechecked": True,
        "candidate_hash": metadata["candidate_hash"], "spec_hash": metadata["spec_hash"], "proof_hash": metadata["proof_hash"],
        "targets": [{"id": "conservation", "proposition": "ClusterSpec.Conserves", "status": "passed", "axioms": []}]}
    if mutation == "wrong_hash":
        report["candidate_hash"] = "0" * 64
    elif mutation == "missing_target":
        report["targets"] = []
    elif mutation == "custom_axiom":
        report["targets"][0]["axioms"] = ["ClusterSpec.cheat"]
    elif mutation == "no_kernel_replay":
        report["kernel_rechecked"] = False
    else:
        report["targets"] = {"invalid": "format"}
    assert finish(client, job, proof_report=report).json()["status"] == "failed"
    research_step(app.state.db, app.state.settings, study["id"])
    assert client.get(f"/api/studies/{study['id']}").json()["best_job_id"] is None
    assert client.get(f"/api/studies/{study['id']}/tree").status_code == 200


def test_advisory_check_does_not_claim_proven_but_can_select_measured_candidate(client, app):
    policy = smoke_policy()
    policy["mode"] = "advisory"
    study = client.post("/api/studies", json={"name": "advisory", "objective": "Infrastructure gate smoke", "mode": "benchmark", "rounds": 1, "verification": policy}).json()
    worker = register(client)
    research_step(app.state.db, app.state.settings, study["id"])
    candidate = claim(client, worker)
    finish(client, candidate, metrics={"score": 5})
    research_step(app.state.db, app.state.settings, study["id"])
    verifier = register(client, id="lean-worker", pool_id="lean-cpu")
    proof = claim(client, verifier)
    finish(client, proof, status="failed", proof_report={"status": "unproved", "error": "missing proof input"})
    research_step(app.state.db, app.state.settings, study["id"])
    result = client.get(f"/api/studies/{study['id']}").json()
    assert result["status"] == "completed" and result["best_job_id"] == candidate["id"]
    assert client.get(f"/api/jobs/{candidate['id']}/verification").json()["verification"]["status"] == "unproved"


def test_repair_is_bounded_and_cancellation_fences_model_output(client, app, monkeypatch):
    from research_cluster import verification
    study, candidate, check = prepared(client, app)
    with app.state.db.transaction() as session:
        row = session.get(Verification, check["id"])
        row.policy = {**row.policy, "repair_attempts": 1}
        row.status, row.report = "unproved", {"error": "proof needed"}
        session.get(Job, row.job_ids[-1]).status = "failed"
    monkeypatch.setenv("LLM_MODEL", "test/model")
    monkeypatch.setenv("LLM_API_KEY", "not-an-actual-key")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def generator(*args):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return json.dumps({"conservation": "by intro a b k h; omega"})

    monkeypatch.setattr(verification, "generate_proofs", generator)
    thread = threading.Thread(target=verification_step, args=(app.state.db, app.state.settings, study["id"]))
    thread.start()
    try:
        assert entered.wait(5)
        tree = client.get(f"/api/studies/{study['id']}/tree").json()
        assert tree["summary"]["running_model_steps"] == 1
        client.post(f"/api/studies/{study['id']}/cancel")
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    after = client.get(f"/api/jobs/{candidate['id']}/verification").json()["verification"]
    assert after["status"] == "canceled" and after["model_attempts"] == 1 and len(after["job_ids"]) == 1
    verification_step(app.state.db, app.state.settings, study["id"])
    assert len(calls) == 1


def test_proposals_have_reviewable_formal_assessments(client, app, offline_literature):
    study = create_and_plan(client, app)
    task = study["config"]["literature"]["tasks"][0]
    assert task["formal_verification"]["status"] == "unassessed"
    endpoint = f"/api/studies/{study['id']}/proposals/{task['id']}/formal-verification"
    response = client.put(endpoint, json={"status": "required", "rationale": "Preserve an explicit sampling property", "obligations": ["State the distribution and assumptions"]})
    assert response.status_code == 200
    assert response.json()["config"]["literature"]["tasks"][0]["formal_verification"]["status"] == "required"
    invalid = client.post("/api/studies", json={"name": "no contract", "objective": "Missing target", "verification": {"mode": "required"}})
    assert invalid.status_code == 422
    gpu_policy = smoke_policy()
    gpu_policy["pool_id"] = "gpu-default"
    assert client.post("/api/studies", json={"name": "wrong pool", "objective": "CPU only", "verification": gpu_policy}).status_code == 422


def test_real_lean_checks_frozen_propositions_and_replays_kernel(tmp_path, lean_home):
    inputs(tmp_path, {"conservation": "by intro a b k h; omega"})
    report = verify(tmp_path, lean_home)
    assert report["status"] == "passed" and report["kernel_rechecked"] is True
    assert report["targets"][0]["proposition"] == "ClusterSpec.Conserves"
    assert "--fresh ClusterProofs" in (tmp_path / "artifacts/lean-check.log").read_text()


@pytest.mark.parametrize("term", ["by sorry", "by admit", "by run_tac pure ()", "by exact True.intro",
    "by intro a b k h; omega)\nexample : True := (by trivial"])
def test_real_lean_rejects_holes_metaprogramming_and_wrong_propositions(tmp_path, lean_home, term):
    inputs(tmp_path, {"conservation": term})
    report = verify(tmp_path, lean_home)
    assert report["status"] == "unproved" and report["kernel_rechecked"] is False


def test_real_lean_rejects_transitive_custom_axioms(tmp_path, lean_home):
    spec = smoke_policy()["spec_source"] + "\naxiom ClusterSpec.cheat : ClusterSpec.Conserves\n"
    inputs(tmp_path, {"conservation": "ClusterSpec.cheat"}, spec)
    report = verify(tmp_path, lean_home)
    assert report["status"] == "unproved"
    assert report["targets"][0]["axioms"] == ["ClusterSpec.cheat"]


def test_real_lean_rejects_changed_frozen_spec(tmp_path, lean_home):
    inputs(tmp_path, {"conservation": "by intro a b k h; omega"})
    (tmp_path / "ClusterSpec.lean").write_text("def changed := True")
    report = verify(tmp_path, lean_home)
    assert report["status"] == "error" and "hash mismatch" in report["error"]


@pytest.fixture
def lean_runtime(runtime, tmp_path, lean_home):
    client, db, settings = runtime
    worker = ExecutionWorker(str(client.base_url), "lean-cpu", cpus=1, memory_mb=1024, concurrency=1,
        cpu_offset=1, directory=tmp_path / "lean-worker", lean_home=lean_home, report_inventory=False)
    worker_thread = threading.Thread(target=worker.run, daemon=True)
    worker_thread.start()
    wait_for(lambda: any(item["pool_id"] == "lean-cpu" for item in client.get("/api/state").json()["workers"]))
    stop = threading.Event()
    orchestrator = threading.Thread(target=run_local_orchestrator, args=(settings, stop), daemon=True)
    orchestrator.start()
    try:
        yield runtime
    finally:
        stop.set()
        orchestrator.join(10)
        worker.stop.set()
        worker_thread.join(10)
        assert not orchestrator.is_alive() and not worker_thread.is_alive()


def test_real_independent_cpu_queue_proves_candidate_before_promotion(lean_runtime):
    client, _, _ = lean_runtime
    study = client.post("/api/verification/smoke").json()
    result = wait_for(lambda: (s if (s := client.get(f"/api/studies/{study['id']}").json())["status"] in {"completed", "failed"} else None), timeout=150)
    assert result["status"] == "completed", result
    candidate = client.get(f"/api/jobs/{result['best_job_id']}").json()
    assert candidate["metrics"]["score"] == 1
    proof = client.get(f"/api/jobs/{candidate['id']}/verification").json()
    assert proof["verification"]["status"] == "passed"
    assert proof["verification"]["report"]["kernel_rechecked"] is True
    assert proof["checks"][0]["pool_id"] == "lean-cpu"
    check_job = proof["checks"][0]
    assert client.get(f"/api/jobs/{check_job['id']}/artifacts/artifacts/ClusterProofs.lean").status_code == 200
    tree = client.get(f"/api/studies/{study['id']}/tree").json()
    assert tree["summary"]["candidates"] == 1
    assert any(node["kind"] == "obligation" and node["status"] == "passed" for node in tree["nodes"])
