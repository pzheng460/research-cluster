import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from research_cluster.research import research_step
from research_cluster.worker import ExecutionWorker


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError("Timed out waiting for state transition")


@pytest.fixture
def runtime(app, tmp_path, request):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    wait_for(lambda: server.started)
    capacity = {"cpus": 1, "memory_mb": 512, "concurrency": 1, **getattr(request, "param", {})}
    worker = ExecutionWorker(url, "cpu-default", **capacity,
                             directory=tmp_path / "worker", report_inventory=False)
    worker_thread = threading.Thread(target=worker.run, daemon=True)
    worker_thread.start()
    client = httpx.Client(base_url=url, timeout=5)
    wait_for(lambda: client.get("/api/state").json()["workers"])
    try:
        yield client, app.state.db, app.state.settings
    finally:
        worker.stop.set()
        worker_thread.join(10)
        assert not worker_thread.is_alive(), "Worker failed to stop"
        client.close()
        server.should_exit = True
        thread.join(10)
        sock.close()


def test_real_execution_metrics_artifacts_and_independent_gpu_queue(runtime):
    client, _, _ = runtime
    gpu = client.post("/api/jobs", json={"name": "waiting-gpu", "kind": "gpu", "pool_id": "gpu-default", "resources": {"gpus": 1}}).json()
    code = "import json\nfrom pathlib import Path\nPath('metrics.json').write_text(json.dumps({'score':42.5}))\nprint('actual worker output')\n"
    job = client.post("/api/jobs", json={"name": "real-cpu", "files": {"main.py": code}}).json()
    result = wait_for(lambda: (j if (j := client.get(f"/api/jobs/{job['id']}").json())["status"] == "succeeded" else None))
    assert result["metrics"] == {"score": 42.5}
    assert "actual worker output" in result["logs"]
    artifact = client.get(f"/api/jobs/{job['id']}/artifacts/metrics.json")
    assert artifact.json() == {"score": 42.5}
    assert client.get(f"/api/jobs/{gpu['id']}").json()["status"] == "queued"


def test_real_running_cancellation(runtime):
    client, _, _ = runtime
    job = client.post("/api/jobs", json={"name": "cancel-me", "files": {"main.py": "import time\nprint('started',flush=True)\ntime.sleep(120)\n"}}).json()
    wait_for(lambda: "started" in client.get(f"/api/jobs/{job['id']}").json()["logs"])
    client.post(f"/api/jobs/{job['id']}/cancel")
    result = wait_for(lambda: (j if (j := client.get(f"/api/jobs/{job['id']}").json())["status"] == "canceled" else None))
    assert result["finished_at"] is not None


def test_real_process_timeout(runtime):
    client, _, _ = runtime
    job = client.post("/api/jobs", json={"name": "timeout", "timeout_seconds": 1, "files": {"main.py": "import time\ntime.sleep(120)"}}).json()
    result = wait_for(lambda: (j if (j := client.get(f"/api/jobs/{job['id']}").json())["status"] == "failed" else None))
    assert "时限" in result["error"]


def test_complete_research_loop_runs_and_archives_best_candidate(runtime):
    client, db, settings = runtime
    study = client.post("/api/studies", json={"name": "actual-research", "objective": "improve convergence", "mode": "benchmark", "rounds": 3}).json()

    def step():
        research_step(db, settings, study["id"])
        return next(s for s in client.get("/api/state").json()["studies"] if s["id"] == study["id"])

    result = wait_for(lambda: (s if (s := step())["status"] == "completed" else None), timeout=30)
    jobs = client.get("/api/state").json()["jobs"]
    assert len(jobs) == 3 and all(j["status"] == "succeeded" for j in jobs)
    assert result["best_score"] == max(j["metrics"]["score"] for j in jobs)
    ordered = sorted(jobs, key=lambda j: j["round"])
    assert ordered[1]["parent_id"] == ordered[0]["id"]
