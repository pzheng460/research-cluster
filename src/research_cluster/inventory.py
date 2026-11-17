"""Read-only host inventory, independent from execution-pool reservations.

This probe uses the OS and nvidia-smi, never imports a CUDA library, reserves
devices, or launches experiments. It can run on a host without any worker.
"""
import csv
import hashlib
import io
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading

import httpx

log = logging.getLogger(__name__)


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def cpu_quota(cgroup_root=Path("/sys/fs/cgroup"), membership=None):
    """Minimum CFS bandwidth limit in the visible cgroup hierarchy, v1 or v2."""
    membership = read("/proc/self/cgroup") if membership is None else membership
    root, directories = Path(cgroup_root), set()
    entries = [(root, "")]
    for line in membership.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, relative = parts[1].split(","), parts[2].lstrip("/")
        if parts[1] == "":
            entries.append((root, relative))
        elif "cpu" in controllers:
            for name in ("cpu", "cpu,cpuacct", "cpuacct,cpu"):
                entries.append((root / name, relative))
    # Container cgroup mounts may already be rooted at the process's cgroup.
    entries += [(root / name, "") for name in ("cpu", "cpu,cpuacct", "cpuacct,cpu")]
    for base, relative in entries:
        current = base / relative
        while current.is_relative_to(base):
            directories.add(current)
            if current == base:
                break
            current = current.parent
    limits = []
    for directory in directories:
        try:
            value = read(directory / "cpu.max").split()
            if len(value) == 2 and value[0] != "max" and float(value[1]) > 0:
                limits.append(float(value[0]) / float(value[1]))
            quota = read(directory / "cpu.cfs_quota_us")
            period = read(directory / "cpu.cfs_period_us")
            if quota and period and float(quota) > 0 and float(period) > 0:
                limits.append(float(quota) / float(period))
        except ValueError:
            continue
    return min((value for value in limits if value > 0), default=None)


def physical_cpus(cpu_root=Path("/sys/devices/system/cpu")):
    cores = set()
    for cpu in Path(cpu_root).glob("cpu[0-9]*"):
        package = read(cpu / "topology/physical_package_id")
        core = read(cpu / "topology/core_id")
        if package and core:
            cores.add((package, core))
    return len(cores) or None


def gpu_inventory(environment=None):
    environment = os.environ if environment is None else environment
    masks = [environment.get(name) for name in ("NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")]
    if any(mask is not None and mask.strip().lower() in {"", "none", "void", "-1"} for mask in masks):
        return {"gpus": [], "gpu_status": "restricted", "gpu_error": None}
    binary = shutil.which("nvidia-smi")
    if not binary:
        return {"gpus": [], "gpu_status": "unavailable", "gpu_error": "执行主机未提供 nvidia-smi"}
    try:
        result = subprocess.run([binary, "--query-gpu=index,uuid,name,memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise ValueError(result.stderr.strip()[:300] or "GPU 驱动查询失败")
        devices = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if not row:
                continue
            if len(row) != 4:
                raise ValueError("无法解析 GPU 设备清单")
            index, identity, name, memory = (part.strip() for part in row)
            devices.append({"index": index, "uuid": identity, "name": name,
                            "memory_mb": int(float(memory)) if memory not in {"[N/A]", "N/A"} else None})
        # NVIDIA visibility uses physical indices/UUIDs. CUDA indices refer to
        # the device order remaining after the NVIDIA visibility restriction.
        nvidia = environment.get("NVIDIA_VISIBLE_DEVICES")
        if nvidia is not None and nvidia.strip().lower() != "all":
            names = [value.strip() for value in nvidia.split(",")]
            selected = [device for name in names for device in devices
                        if device["index"] == name or device["uuid"].startswith(name)]
            if len(selected) != len(names):
                raise ValueError("无法解析 NVIDIA_VISIBLE_DEVICES 的设备映射")
            devices = selected
        cuda = environment.get("CUDA_VISIBLE_DEVICES")
        if cuda is not None and cuda.strip().lower() != "all":
            names = [value.strip() for value in cuda.split(",")]
            selected = []
            for name in names:
                if name.isdigit() and int(name) < len(devices):
                    selected.append(devices[int(name)])
                else:
                    matches = [device for device in devices if device["uuid"].startswith(name)]
                    if len(matches) != 1:
                        raise ValueError("无法解析 CUDA_VISIBLE_DEVICES 的设备映射")
                    selected.extend(matches)
            devices = selected
        devices = list({device["uuid"]: device for device in devices}.values())
        return {"gpus": devices, "gpu_status": "ok", "gpu_error": None}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {"gpus": [], "gpu_status": "error", "gpu_error": str(error)[:300]}


def probe(include_gpus=True):
    logical = os.cpu_count() or 1
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(logical))
    quota = cpu_quota()
    identity = (os.getenv("CLUSTER_HOST_ID") or read("/sys/class/dmi/id/product_uuid")
                or read("/proc/sys/kernel/random/boot_id") or socket.gethostname())
    host_id = digest(identity)
    cpu_scope = digest("|".join([host_id, read("/proc/self/cgroup"), str(affinity)]))
    scope = "|".join([cpu_scope, str(include_gpus), repr(os.getenv("CUDA_VISIBLE_DEVICES")), repr(os.getenv("NVIDIA_VISIBLE_DEVICES"))])
    return {"id": digest(scope), "host_id": host_id, "hostname": socket.gethostname(),
            "cpu_scope": cpu_scope,
            "cpu_logical": logical, "cpu_physical": physical_cpus(),
            "cpu_affinity": len(affinity), "cpu_quota": quota,
            "cpu_effective": min(len(affinity), quota) if quota is not None else len(affinity),
            **(gpu_inventory() if include_gpus else {"gpus": [], "gpu_status": "unavailable", "gpu_error": None})}


def run_monitor(url, token="", interval=15, stop=None):
    stop = stop or threading.Event()
    with httpx.Client(base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=10) as client:
        while not stop.is_set():
            try:
                response = client.post("/api/inventory", json=probe())
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning("Hardware inventory could not reach the control service")
            stop.wait(interval)


def save_inventory(session, value):
    from .models import Inventory, now
    # Both monitor and worker can report one scope concurrently.
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    values = {"id": value.id, "host_id": value.host_id, "hostname": value.hostname,
              "details": value.model_dump(), "last_seen": now()}
    statement = insert(Inventory).values(**values)
    session.execute(statement.on_conflict_do_update(index_elements=["id"], set_=values))


def summarize(records):
    from .models import now
    hosts = {}
    for record in sorted(records, key=lambda value: value.last_seen, reverse=True):
        if now() - record.last_seen > 90:
            continue
        host = hosts.setdefault(record.host_id, {"id": record.host_id, "hostname": record.hostname,
            "cpu_logical": 0, "cpu_physical": None, "scopes": [], "gpus": {}, "gpu_known": False,
            "gpu_errors": [], "last_seen": 0})
        data = record.details
        host["cpu_logical"] = max(host["cpu_logical"], data["cpu_logical"])
        if data.get("cpu_physical") is not None:
            host["cpu_physical"] = max(host["cpu_physical"] or 0, data["cpu_physical"])
        scope_id = data.get("cpu_scope") or data["id"]
        if not any(scope["id"] == scope_id for scope in host["scopes"]):
            host["scopes"].append({"id": scope_id, **{key: data[key] for key in ("cpu_affinity", "cpu_quota", "cpu_effective")}})
        host["last_seen"] = max(host["last_seen"], record.last_seen)
        if data["gpu_status"] in {"ok", "restricted"}:
            host["gpu_known"] = True
        elif data.get("gpu_error"):
            host["gpu_errors"].append(data["gpu_error"])
        for gpu in data["gpus"]:
            host["gpus"][gpu["uuid"]] = gpu
    nodes = []
    for host in hosts.values():
        host["gpus"] = list(host["gpus"].values())
        host["scopes"].sort(key=lambda value: value["cpu_effective"], reverse=True)
        nodes.append(host)
    known = [host for host in nodes if host["gpu_known"]]
    gpu_ids = {gpu["uuid"] for host in nodes for gpu in host["gpus"]}
    return {"nodes": nodes, "cpu_logical": sum(h["cpu_logical"] for h in nodes) if nodes else None,
            "cpu_physical": sum(h["cpu_physical"] for h in nodes) if nodes and all(h["cpu_physical"] is not None for h in nodes) else None,
            "gpu_count": len(gpu_ids) if known else None,
            "gpu_complete": len(known) == len(nodes) and bool(nodes),
            "last_seen": max((h["last_seen"] for h in nodes), default=None)}
