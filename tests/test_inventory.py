from types import SimpleNamespace

import pytest

from research_cluster import inventory
from research_cluster.models import Inventory, now
from conftest import hardware_report, register


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_cgroup_v1_cpu_quota(tmp_path):
    write(tmp_path / "cpu/cpu.cfs_quota_us", "9600000")
    write(tmp_path / "cpu/cpu.cfs_period_us", "100000")
    assert inventory.cpu_quota(tmp_path, "10:cpu,cpuacct:/container") == 96
    write(tmp_path / "cpu/cpu.cfs_quota_us", "-1")
    assert inventory.cpu_quota(tmp_path, "10:cpu,cpuacct:/container") is None


def test_cgroup_v2_inherits_parent_and_fractional_limits(tmp_path):
    write(tmp_path / "cpu.max", "max 100000")
    write(tmp_path / "tenant/cpu.max", "800000 100000")
    write(tmp_path / "tenant/job/cpu.max", "250000 100000")
    assert inventory.cpu_quota(tmp_path, "0::/tenant/job") == 2.5
    write(tmp_path / "tenant/job/cpu.max", "max 100000")
    assert inventory.cpu_quota(tmp_path, "0::/tenant/job") == 8


def test_physical_cores_deduplicate_hyperthreads(tmp_path):
    for index, (package, core) in enumerate([(0, 0), (0, 0), (1, 0), (1, 0)]):
        write(tmp_path / f"cpu{index}/topology/physical_package_id", str(package))
        write(tmp_path / f"cpu{index}/topology/core_id", str(core))
    assert inventory.physical_cpus(tmp_path) == 2


def test_probe_keeps_logical_affinity_quota_and_physical_separate(monkeypatch):
    monkeypatch.setattr(inventory.os, "cpu_count", lambda: 192)
    monkeypatch.setattr(inventory.os, "sched_getaffinity", lambda _: set(range(8)))
    monkeypatch.setattr(inventory, "cpu_quota", lambda: 96)
    monkeypatch.setattr(inventory, "physical_cpus", lambda: 96)
    def forbid_gpu():
        raise AssertionError("CPU worker must not query GPU inventory")
    monkeypatch.setattr(inventory, "gpu_inventory", forbid_gpu)
    value = inventory.probe(include_gpus=False)
    assert (value["cpu_logical"], value["cpu_physical"], value["cpu_affinity"], value["cpu_quota"], value["cpu_effective"]) == (192, 96, 8, 96, 8)


@pytest.fixture
def gpu_query(monkeypatch):
    monkeypatch.setattr(inventory.shutil, "which", lambda _: "/test/nvidia-smi")
    monkeypatch.setattr(inventory.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0,
        stdout="\n".join(f"{i}, GPU-test-{i}, NVIDIA Test GPU, 16384" for i in range(8)), stderr=""))


def test_gpu_inventory_reads_actual_device_rows(gpu_query):
    result = inventory.gpu_inventory({})
    assert result["gpu_status"] == "ok" and len(result["gpus"]) == 8


def test_gpu_visibility_masks_are_applied_in_device_order(gpu_query):
    result = inventory.gpu_inventory({"NVIDIA_VISIBLE_DEVICES": "4,6", "CUDA_VISIBLE_DEVICES": "1,0"})
    assert [gpu["index"] for gpu in result["gpus"]] == ["6", "4"]
    assert inventory.gpu_inventory({"CUDA_VISIBLE_DEVICES": ""})["gpu_status"] == "restricted"


def test_missing_gpu_probe_is_unknown_not_zero(client, monkeypatch):
    monkeypatch.setattr(inventory.shutil, "which", lambda _: None)
    result = inventory.gpu_inventory({})
    assert result["gpu_status"] == "unavailable"
    response = client.post("/api/inventory", json=hardware_report(**result))
    assert response.status_code == 200, response.text
    assert client.get("/api/state").json()["hardware"]["gpu_count"] is None


def test_hardware_counts_do_not_use_worker_reservations_and_deduplicate_hosts(client):
    register(client, cpus=2)
    register(client, "gpu", gpu_ids=["0"])
    for report in [hardware_report(), hardware_report(id="inventory-b")]:
        assert client.post("/api/inventory", json=report).status_code == 200
    state = client.get("/api/state").json()
    hardware = state["hardware"]
    assert hardware["cpu_logical"] == 192
    assert hardware["cpu_physical"] == 96
    assert hardware["gpu_count"] == 8
    assert len(hardware["nodes"]) == 1 and len(hardware["nodes"][0]["scopes"]) == 1
    assert state["workers"][0]["cpus"] == 2  # Reservation remains separate.
    assert client.post("/api/inventory", json=hardware_report(cpu_quota=48, cpu_effective=48)).status_code == 200
    hardware = client.get("/api/state").json()["hardware"]
    assert hardware["cpu_logical"] == 192
    assert hardware["nodes"][0]["scopes"][0]["cpu_effective"] == 48


def test_stale_inventory_is_not_reported_as_current_hardware(client, app):
    client.post("/api/inventory", json=hardware_report())
    with app.state.db.transaction() as session:
        session.get(Inventory, "inventory-a").last_seen = now() - 91
    hardware = client.get("/api/state").json()["hardware"]
    assert hardware["cpu_logical"] is None and hardware["gpu_count"] is None


def test_clean_worker_shutdown_removes_its_quota_immediately(client):
    identity, headers = register(client)
    assert client.post(f"/api/workers/{identity}/offline", headers=headers).status_code == 200
    assert not client.get("/api/state").json()["workers"][0]["online"]
