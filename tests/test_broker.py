import concurrent.futures
import io
import zipfile

import pytest

from research_cluster.api import create_app
from research_cluster.bundles import unpack_files
from research_cluster.config import Settings
from research_cluster.models import Job, now

from conftest import claim, finish, register, submit


def test_cpu_gpu_queues_and_pool_pausing_are_independent(client):
    cpu, gpu = register(client), register(client, "gpu")
    cpu_job, gpu_job = submit(client), submit(client, "gpu")
    client.post("/api/pools/gpu-default/toggle")
    assert claim(client, gpu) is None
    claimed = claim(client, cpu)
    assert claimed["id"] == cpu_job["id"]
    assert claimed["spec"]["assigned_gpu_ids"] == []
    client.post("/api/pools/gpu-default/toggle")
    gpu_claim = claim(client, gpu)
    assert gpu_claim["id"] == gpu_job["id"]
    assert gpu_claim["spec"]["assigned_gpu_ids"] == ["0"]
    assert "lease_token" not in client.get(f"/api/jobs/{gpu_claim['id']}").json()


def test_capacity_and_gpu_exclusive_allocation(client):
    worker = register(client, "gpu")
    for _ in range(3):
        submit(client, "gpu")
    first, second = claim(client, worker), claim(client, worker)
    assert first["spec"]["assigned_gpu_ids"] == ["0"]
    assert second["spec"]["assigned_gpu_ids"] == ["1"]
    assert claim(client, worker) is None
    assert finish(client, first).status_code == 200
    third = claim(client, worker)
    assert third["spec"]["assigned_gpu_ids"] == ["0"]


def test_concurrent_claims_cannot_oversubscribe(client):
    worker = register(client, cpus=1, memory_mb=512)
    for _ in range(5):
        submit(client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: claim(client, worker), range(5)))
    assert len([x for x in results if x]) == 1


def test_idempotency_and_mismatch(client):
    first = submit(client, idempotency_key="same-input")
    assert submit(client, idempotency_key="same-input")["id"] == first["id"]
    response = client.post("/api/jobs", json={"name": "different", "idempotency_key": "same-input"})
    assert response.status_code == 409


def test_expired_lease_cannot_overwrite_new_attempt(client, app):
    worker = register(client)
    submit(client)
    old = claim(client, worker)
    with app.state.db.transaction() as session:
        session.get(Job, old["id"]).lease_until = now() - 6
    client.get("/api/state")
    new = claim(client, worker)
    assert new["id"] == old["id"] and new["attempt"] == 2
    assert finish(client, old).status_code == 409
    assert finish(client, new).status_code == 200


def test_log_retries_and_terminal_completion_are_idempotent(client):
    worker = register(client)
    submit(client)
    job = claim(client, worker)
    message = {"lease_token": job["lease_token"], "offset": 0, "logs": "hello\n"}
    for _ in range(2):
        assert client.post(f"/api/jobs/{job['id']}/heartbeat", json=message).status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").json()["logs"] == "hello\n"
    assert finish(client, job, offset=6).status_code == 200
    assert finish(client, job, offset=6).status_code == 200
    events = client.get("/api/state").json()["events"]
    assert len([e for e in events if e["kind"] == "job.succeeded"]) == 1


def test_cancellation_wins_over_late_success(client):
    queued = submit(client)
    assert client.post(f"/api/jobs/{queued['id']}/cancel").json()["status"] == "canceled"
    worker = register(client)
    submit(client)
    running = claim(client, worker)
    client.post(f"/api/jobs/{running['id']}/cancel")
    result = finish(client, running)
    assert result.json()["status"] == "canceled"


def test_resource_mismatches_and_traversal_are_rejected(client):
    assert client.post("/api/jobs", json={"name": "x", "kind": "gpu", "pool_id": "cpu-default", "resources": {"gpus": 1}}).status_code == 422
    assert client.post("/api/jobs", json={"name": "x", "files": {"../escape": "bad"}}).status_code == 422
    assert client.post("/api/workers/register", json={"id": "cpu-x", "name": "x", "kind": "cpu", "pool_id": "cpu-default", "cpus": 1, "memory_mb": 512, "gpu_ids": ["0"]}).status_code == 422


def test_archives_cannot_escape_destination(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        unpack_files(stream.getvalue(), tmp_path / "target")
    assert not (tmp_path / "escape.txt").exists()


def test_authentication_and_cross_origin_protection(tmp_path):
    from fastapi.testclient import TestClient
    app = create_app(Settings(data_dir=tmp_path, token="secret"))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/state", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert client.post("/api/jobs", json={"name": "bad"}, headers={"Authorization": "Bearer secret", "Origin": "https://other.example"}).status_code == 403
