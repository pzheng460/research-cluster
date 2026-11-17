"""Remote execution worker. CPU and GPU workers run as independent processes.

Only this module launches experiment processes. The API/orchestrator never
imports CUDA, contacts a device, or waits synchronously for an experiment.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import httpx

from .bundles import pack_files, unpack_files
from .inventory import probe

log = logging.getLogger(__name__)


class LogBuffer:
    def __init__(self):
        self.text, self.dropped = "", 0
        self.lock = threading.Lock()

    def write(self, text):
        with self.lock:
            capacity = 60000 - len(self.text)
            self.text += text[:max(0, capacity)]
            self.dropped += max(0, len(text) - max(0, capacity))

    def take(self):
        with self.lock:
            text = self.text
            if self.dropped:
                text += f"\n[省略 {self.dropped} 字符的高频输出]\n"
            self.text, self.dropped = "", 0
            return text


class ExecutionWorker:
    def __init__(self, url, pool, kind="cpu", *, cpus=2, memory_mb=2048, gpu_ids=None,
                 concurrency=2, token="", directory=None, runtime="native", image="python:3.12-slim", name=None,
                 report_inventory=True, lean_home=None, cpu_offset=0):
        self.url = url.rstrip("/")
        self.pool, self.kind = pool, kind
        self.cpus, self.memory_mb, self.gpu_ids = cpus, memory_mb, gpu_ids or []
        self.concurrency, self.token = concurrency, token
        self.directory = Path(directory or ".state/worker").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runtime, self.image = runtime, image
        self.report_inventory = report_inventory
        self.identity = f"{kind}-{uuid.uuid4().hex[:12]}"
        self.name = name or f"{socket.gethostname()} / {kind.upper()}"
        self.secret = ""
        self.stop = threading.Event()
        self.cpu_inventory = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(os.cpu_count() or 1))
        self.cpu_inventory = self.cpu_inventory[cpu_offset:cpu_offset + cpus]
        if cpus > len(self.cpu_inventory):
            raise ValueError("Requested worker CPUs exceed available CPU affinity")
        if kind == "gpu" and not self.gpu_ids:
            raise ValueError("GPU workers require --gpu-ids")
        if kind == "cpu" and self.gpu_ids:
            raise ValueError("CPU workers cannot own GPU devices")
        if runtime == "docker" and not shutil.which("docker"):
            raise ValueError("Docker runtime requires docker on the execution host")
        self.allocations = {}
        self.allocation_lock = threading.Lock()
        self.lean_home = Path(lean_home or os.getenv("CLUSTER_LEAN_HOME", ".state/toolchains/lean-4.35.0-linux")).resolve()

    def client(self):
        return httpx.Client(base_url=self.url, headers={"Authorization": f"Bearer {self.token}"} if self.token else {}, timeout=15)

    def register(self, client):
        response = client.post("/api/workers/register", json={
            "id": self.identity, "name": self.name, "pool_id": self.pool, "kind": self.kind,
            "cpus": self.cpus, "memory_mb": self.memory_mb, "gpu_ids": self.gpu_ids,
            "concurrency": self.concurrency,
            "inventory": probe(include_gpus=self.kind == "gpu") if self.report_inventory else None,
        })
        response.raise_for_status()
        self.secret = response.json()["worker_token"]

    def run(self):
        with self.client() as client:
            self.register(client)
            log.info("Worker %s connected to %s (%s)", self.identity, self.pool, self.runtime)
            try:
                with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                    tasks, reported = set(), time.monotonic()
                    while not self.stop.is_set():
                        for future in list(tasks):
                            if future.done():
                                tasks.remove(future)
                                try:
                                    future.result()
                                except Exception:
                                    log.exception("Experiment execution failed")
                        try:
                            if self.report_inventory and time.monotonic() - reported > 30:
                                response = client.post("/api/inventory", json=probe(include_gpus=self.kind == "gpu"))
                                response.raise_for_status()
                                reported = time.monotonic()
                            response = client.post(f"/api/workers/{self.identity}/claim", headers={"X-Worker-Token": self.secret})
                            response.raise_for_status()
                            job = response.json()["job"]
                            if job:
                                tasks.add(executor.submit(self.execute, job))
                        except httpx.HTTPError:
                            log.warning("Control service unavailable; retaining only valid local leases")
                        self.stop.wait(1)
            finally:
                try:
                    client.post(f"/api/workers/{self.identity}/offline", headers={"X-Worker-Token": self.secret})
                except httpx.HTTPError:
                    pass

    def execute(self, job):
        identity, lease = job["id"], job["lease_token"]
        spec = job["spec"]
        workspace = self.directory / identity / f"attempt-{job['attempt']}"
        workspace.mkdir(parents=True, exist_ok=True)
        buffer, process, container_name = LogBuffer(), None, f"research-{identity}-{job['attempt']}"
        last_contact, offset, pending = time.monotonic(), 0, ""
        status, error, metrics, proof_report = "failed", None, {}, None
        with self.allocation_lock:
            used = {c for values in self.allocations.values() for c in values}
            cpu_ids = [c for c in self.cpu_inventory if c not in used][:spec["resources"]["cpus"]]
            if len(cpu_ids) != spec["resources"]["cpus"]:
                raise RuntimeError("Local CPU capacity does not match broker allocation")
            self.allocations[identity] = cpu_ids

        def terminate():
            if process:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
                except ProcessLookupError:
                    pass
                # A successful parent may still have background children.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if self.runtime == "docker":
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)

        try:
            with self.client() as client:
                response = client.get(f"/api/jobs/{identity}/bundle", headers={"X-Lease-Token": lease})
                response.raise_for_status()
                unpack_files(response.content, workspace)
                env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
                # Credentials of the control plane and LLM provider never enter an experiment.
                env.update({"PYTHONUNBUFFERED": "1", "CUDA_VISIBLE_DEVICES": ",".join(spec.get("assigned_gpu_ids", [])),
                            "OMP_NUM_THREADS": str(len(cpu_ids)), "OPENBLAS_NUM_THREADS": str(len(cpu_ids)),
                            "CLUSTER_JOB_ID": identity, "CLUSTER_OUTPUT_DIR": str(workspace / "artifacts")})
                command = list(spec["command"])
                if spec.get("verification"):
                    command = ["python", "-I", "-m", "research_cluster.lean_runner", "--lean-home", str(self.lean_home)]
                if self.runtime == "docker":
                    command = ["docker", "run", "--rm", "--name", container_name, "--network", "none",
                               "--cpus", str(spec["resources"]["cpus"]), "--cpuset-cpus", ",".join(map(str, cpu_ids)),
                               "--memory", f"{spec['resources']['memory_mb']}m", "--pids-limit", "256",
                               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                               "--mount", f"type=bind,src={workspace},dst=/workspace", "--workdir", "/workspace",
                               "--env", "PYTHONUNBUFFERED=1", "--env", f"OMP_NUM_THREADS={len(cpu_ids)}",
                               "--env", "CLUSTER_OUTPUT_DIR=/workspace/artifacts"] + (
                               ["--gpus", '"device=' + ",".join(spec["assigned_gpu_ids"]) + '"'] if self.kind == "gpu" else
                               ["--env", "CUDA_VISIBLE_DEVICES=", "--env", "NVIDIA_VISIBLE_DEVICES=void"]) + [self.image] + command
                elif command[0] in {"python", "python3"}:
                    command[0] = sys.executable
                buffer.write(f"[{self.kind.upper()} worker] {' '.join(spec['command'])}\n")
                process = subprocess.Popen(command, cwd=workspace, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                if self.runtime == "native" and hasattr(os, "sched_setaffinity"):
                    try:
                        os.sched_setaffinity(process.pid, cpu_ids)
                    except ProcessLookupError:
                        pass

                def read_output():
                    while data := process.stdout.read1(4096):
                        buffer.write(data.decode("utf-8", errors="replace"))

                reader = threading.Thread(target=read_output, daemon=True)
                reader.start()
                started, last_heartbeat = time.monotonic(), 0
                while True:
                    current = time.monotonic()
                    if self.stop.is_set():
                        error, status = "Worker 正在停止", "canceled"
                        terminate()
                        break
                    if current - started > spec["timeout_seconds"]:
                        error = f"实验超过 {spec['timeout_seconds']} 秒时限"
                        terminate()
                        break
                    if current - last_contact > job["lease_seconds"] - 2:
                        error = "Control connection lost; execution stopped before lease expiry"
                        terminate()
                        break
                    if current - last_heartbeat >= 2:
                        if not pending:
                            pending = buffer.take()
                        try:
                            response = client.post(f"/api/jobs/{identity}/heartbeat", json={"lease_token": lease, "logs": pending, "offset": offset})
                            if response.status_code == 409:
                                error = "Execution lease revoked"
                                terminate()
                                break
                            response.raise_for_status()
                            result = response.json()
                            offset, pending, last_contact = result["offset"], "", time.monotonic()
                            if result["cancel"]:
                                error, status = "用户取消实验", "canceled"
                                terminate()
                                break
                        except httpx.HTTPError:
                            pass
                        last_heartbeat = current
                    if process.poll() is not None:
                        status = "succeeded" if process.returncode == 0 else "failed"
                        error = None if status == "succeeded" else f"进程退出码 {process.returncode}"
                        break
                    self.stop.wait(0.1)
                reader.join(timeout=2)
                if spec.get("verification"):
                    proof_file = workspace / "artifacts" / "verification.json"
                    if proof_file.is_file() and not proof_file.is_symlink() and proof_file.stat().st_size <= 100000:
                        proof_report = json.loads(proof_file.read_text())
                        if proof_report.get("status") != "passed":
                            error = proof_report.get("error", "Lean proof incomplete")
                if status == "succeeded":
                    metric_file = workspace / "metrics.json"
                    if metric_file.is_file():
                        if metric_file.is_symlink() or not metric_file.resolve().is_relative_to(workspace):
                            raise ValueError("metrics.json must be a regular workspace file")
                        if metric_file.stat().st_size > 65536:
                            raise ValueError("metrics.json exceeds 64 KiB")
                        metrics = json.loads(metric_file.read_text())
                        if not isinstance(metrics, dict) or len(metrics) > 100 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in metrics.values()):
                            raise ValueError("metrics.json must contain at most 100 finite numeric metrics")
                artifact_files, artifact_bytes = {}, 0
                artifact_files["run_manifest.json"] = json.dumps({
                    "job_id": identity, "attempt": job["attempt"], "worker_id": self.identity,
                    "kind": self.kind, "pool_id": self.pool, "runtime": self.runtime,
                    "image": self.image if self.runtime == "docker" else None,
                    "command": spec["command"], "resources": spec["resources"],
                    "assigned_gpu_ids": spec.get("assigned_gpu_ids", []),
                }, indent=2).encode()
                candidates = [workspace / "metrics.json", workspace / "RESEARCH_NOTES.md"]
                if (workspace / "artifacts").is_dir() and not (workspace / "artifacts").is_symlink():
                    candidates += list((workspace / "artifacts").rglob("*"))[:2000]
                for path in candidates:
                    if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(workspace):
                        artifact_bytes += path.stat().st_size
                        if artifact_bytes > 16 * 1024 * 1024:
                            raise ValueError("Artifacts exceed the 16 MiB worker limit")
                        artifact_files[path.relative_to(workspace).as_posix()] = path.read_bytes()
                if artifact_files:
                    response = client.post(f"/api/jobs/{identity}/artifacts", content=pack_files(artifact_files),
                                           headers={"X-Lease-Token": lease, "Content-Type": "application/zip"})
                    response.raise_for_status()
        except Exception as exception:
            status, error = "failed", str(exception)[:2000]
            buffer.write(f"\n[worker error] {error}\n")
        finally:
            try:
                terminate()
            finally:
                with self.allocation_lock:
                    self.allocations.pop(identity, None)
            remaining = buffer.take()
            # A pending unacknowledged chunk is always resent at its original offset.
            chunks = [pending] if pending else []
            chunks += [remaining[i:i+60000] for i in range(0, len(remaining), 60000)]
            with self.client() as client:
                deadline = last_contact + job["lease_seconds"] - 1
                for chunk in chunks:
                    while time.monotonic() < deadline:
                        try:
                            response = client.post(f"/api/jobs/{identity}/heartbeat", json={"lease_token": lease, "logs": chunk, "offset": offset})
                            if response.status_code == 409:
                                return
                            response.raise_for_status()
                            offset, last_contact = response.json()["offset"], time.monotonic()
                            deadline = last_contact + job["lease_seconds"] - 1
                            break
                        except httpx.HTTPError:
                            self.stop.wait(1)
                    else:
                        return
                while time.monotonic() < deadline:
                    try:
                        response = client.post(f"/api/jobs/{identity}/finish", json={"lease_token": lease, "offset": offset,
                                               "status": status, "metrics": metrics, "error": error, "proof_report": proof_report})
                        if response.status_code == 409:
                            return
                        response.raise_for_status()
                        log.info("%s: %s", identity, status)
                        return
                    except httpx.HTTPError:
                        self.stop.wait(1)
