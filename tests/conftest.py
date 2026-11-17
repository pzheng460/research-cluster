from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from research_cluster.api import create_app
from research_cluster.config import Settings


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path, repo_root=tmp_path))


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def register(client, kind="cpu", **overrides):
    value = {"id": f"{kind}-worker", "name": f"test-{kind}", "kind": kind,
             "pool_id": f"{kind}-default", "cpus": 2, "memory_mb": 1024,
             "gpu_ids": ["0", "1"] if kind == "gpu" else [], "concurrency": 4, **overrides}
    response = client.post("/api/workers/register", json=value)
    assert response.status_code == 201, response.text
    return value["id"], {"X-Worker-Token": response.json()["worker_token"]}


def submit(client, kind="cpu", **overrides):
    value = {"name": "test", "kind": kind, "pool_id": f"{kind}-default",
             "resources": {"cpus": 1, "memory_mb": 512, "gpus": int(kind == "gpu")},
             "files": {"main.py": "print('hello')"}, **overrides}
    response = client.post("/api/jobs", json=value)
    assert response.status_code == 201, response.text
    return response.json()


def claim(client, worker):
    identity, headers = worker
    response = client.post(f"/api/workers/{identity}/claim", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["job"]


def finish(client, job, **overrides):
    return client.post(f"/api/jobs/{job['id']}/finish", json={"lease_token": job["lease_token"],
                        "status": "succeeded", "metrics": {"score": 1}, **overrides})


def hardware_report(**overrides):
    return {"id": "inventory-a", "host_id": "host-a", "hostname": "test-host",
        "cpu_scope": "scope-a", "cpu_logical": 192, "cpu_physical": 96,
        "cpu_affinity": 192, "cpu_quota": 96, "cpu_effective": 96,
        "gpu_status": "ok", "gpus": [{"index": str(i), "uuid": f"GPU-test-{i}",
            "name": "Test GPU", "memory_mb": 16384} for i in range(8)], **overrides}
