import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import httpx

from .config import Settings


def available_cpus():
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1


def main():
    parser = argparse.ArgumentParser(description="Research Cluster · CPU control / independent CPU & GPU execution")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "dev"):
        command = sub.add_parser(name)
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8787)
        if name == "dev":
            command.add_argument("--cpus", type=int, default=min(4, available_cpus()))
            command.add_argument("--memory-mb", type=int, default=2048)
            command.add_argument("--concurrency", type=int, default=2)
            command.add_argument("--lean-cpus", type=int, default=1, help="Additional CPU budget for the independent Lean pool; 0 disables")
            command.add_argument("--lean-memory-mb", type=int, default=1024)
    sub.add_parser("orchestrator")
    monitor = sub.add_parser("monitor", help="Report hardware inventory without reserving resources")
    monitor.add_argument("--url", default="http://127.0.0.1:8787")
    monitor.add_argument("--interval", type=int, default=15)
    worker = sub.add_parser("worker")
    worker.add_argument("--url", default="http://127.0.0.1:8787")
    worker.add_argument("--kind", choices=["cpu", "gpu"], default="cpu")
    worker.add_argument("--pool")
    worker.add_argument("--cpus", type=int, default=min(4, available_cpus()))
    worker.add_argument("--cpu-offset", type=int, default=0, help="Skip CPU affinity entries already assigned to another worker")
    worker.add_argument("--lean-home", type=Path)
    worker.add_argument("--memory-mb", type=int, default=2048)
    worker.add_argument("--concurrency", type=int, default=2)
    worker.add_argument("--gpu-ids", default="")
    worker.add_argument("--runtime", choices=["native", "docker"], default="native")
    worker.add_argument("--image", default="python:3.12-slim")
    worker.add_argument("--directory", default=".state/worker")
    worker.add_argument("--name")
    args = parser.parse_args()
    if getattr(args, "cpu_offset", 0) < 0 or getattr(args, "lean_cpus", 0) < 0:
        parser.error("CPU offsets and Lean CPU budgets cannot be negative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    if args.command in {"serve", "dev"} and args.host not in {"127.0.0.1", "localhost", "::1"} and not settings.token:
        parser.error("Set CLUSTER_TOKEN before binding the command-execution API to a non-loopback address")
    if args.command == "serve":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    elif args.command == "orchestrator":
        if settings.orchestrator == "temporal":
            from .temporal_engine import run_temporal
            from .research import stop_agent_processes
            def stop_temporal(*_):
                stop_agent_processes()
                raise KeyboardInterrupt
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, stop_temporal)
            try:
                asyncio.run(run_temporal(settings))
            except KeyboardInterrupt:
                pass
            finally:
                stop_agent_processes()
        else:
            from .research import run_local_orchestrator
            stop = threading.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: stop.set())
            run_local_orchestrator(settings, stop)
    elif args.command == "monitor":
        from .inventory import run_monitor
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        run_monitor(args.url, token=settings.token, interval=max(5, args.interval), stop=stop)
    elif args.command == "worker":
        from .worker import ExecutionWorker
        instance = ExecutionWorker(args.url, args.pool or f"{args.kind}-default", args.kind,
            cpus=args.cpus, memory_mb=args.memory_mb, concurrency=args.concurrency,
            gpu_ids=[x.strip() for x in args.gpu_ids.split(",") if x.strip()], token=settings.token,
            directory=args.directory, runtime=args.runtime, image=args.image, name=args.name,
            cpu_offset=args.cpu_offset, lean_home=args.lean_home or settings.lean_home)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: instance.stop.set())
        instance.run()
    elif args.command == "dev":
        run_dev(args, settings)


def run_dev(args, settings):
    processes, stop = [], threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    url = f"http://127.0.0.1:{args.port}"
    env = {**os.environ, "CLUSTER_DATA_DIR": str(settings.data_dir), "CLUSTER_DATABASE_URL": settings.database_url,
           "CLUSTER_ORCHESTRATOR": settings.orchestrator, "CLUSTER_REPO_ROOT": str(settings.repo_root),
           "CLUSTER_LEAN_HOME": str(settings.lean_home)}

    def launch(*command):
        process = subprocess.Popen([sys.executable, "-m", "research_cluster", *command], env=env)
        processes.append(process)
        return process

    try:
        launch("serve", "--host", args.host, "--port", str(args.port))
        with httpx.Client(headers={"Authorization": f"Bearer {settings.token}"} if settings.token else {}) as client:
            for _ in range(60):
                try:
                    client.get(url + "/api/health").raise_for_status()
                    break
                except httpx.HTTPError:
                    if stop.wait(0.25) or processes[0].poll() is not None:
                        raise RuntimeError("Control service failed to start")
            else:
                raise RuntimeError("Control service startup timed out")
        launch("orchestrator")
        launch("monitor", "--url", url)
        launch("worker", "--url", url, "--cpus", str(args.cpus), "--memory-mb", str(args.memory_mb),
               "--concurrency", str(args.concurrency), "--directory", str(settings.data_dir / "cpu-worker"))
        if args.lean_cpus and (settings.lean_home / "bin" / "leanchecker").is_file() and args.cpus + args.lean_cpus <= available_cpus():
            launch("worker", "--url", url, "--pool", "lean-cpu", "--cpus", str(args.lean_cpus),
                   "--memory-mb", str(args.lean_memory_mb), "--concurrency", "1", "--cpu-offset", str(args.cpus),
                   "--lean-home", str(settings.lean_home), "--name", "Lean 验证 CPU 节点",
                   "--directory", str(settings.data_dir / "lean-worker"))
        print(f"\nResearch Cluster 管理台: http://{args.host}:{args.port}\n", flush=True)
        if os.getenv("SSH_CONNECTION"):
            print("当前运行在 SSH 远程环境；127.0.0.1 指向服务器。", flush=True)
            print(f"在 VS Code 的 Ports / 端口面板转发 {args.port}，然后打开其 Forwarded Address。", flush=True)
            print(f"也可在自己电脑的终端运行：ssh -N -L 18787:127.0.0.1:{args.port} SSH_HOST", flush=True)
            print("SSH_HOST 使用你平时连接这台服务器的 SSH 别名；转发后访问 http://127.0.0.1:18787/\n", flush=True)
        while not stop.wait(1):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("A service exited; stopping the development stack")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
