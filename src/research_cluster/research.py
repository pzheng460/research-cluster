"""Durable research steps shared by the local and Temporal orchestrators."""
import logging
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import threading
import time

from fastapi import HTTPException
from sqlalchemy import select

from .models import Job, Study, now, uid
from .schemas import JobInput
from .service import RUNNING, TERMINAL, create_job, get_row, reap_expired, record

log = logging.getLogger(__name__)
_agent_processes = set()
_agent_lock = threading.Lock()


def stop_agent_processes():
    with _agent_lock:
        processes = list(_agent_processes)
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def cleanup_agent(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()

EVALUATOR = '''import importlib.util, json, math, random, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("candidate", "candidate.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
def evaluate(seeds):
    scores = []
    for seed in seeds:
        rng = random.Random(seed)
        start = (rng.uniform(-2, 2), rng.uniform(-1, 3))
        point = candidate.optimize(start, 6000)
        if len(point) != 2 or not all(math.isfinite(x) for x in point):
            raise ValueError("Invalid candidate output")
        x, y = point
        loss = (1-x)**2 + 100*(y-x*x)**2
        scores.append(100 / (1 + loss))
    return sum(scores) / len(scores)
started = time.monotonic()
metrics = {"score": evaluate(range(11, 19)), "validation_score": evaluate(range(101, 109))}
metrics["elapsed_seconds"] = time.monotonic()-started
Path("metrics.json").write_text(json.dumps(metrics))
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/metrics.json").write_text(json.dumps(metrics, indent=2))
Path("artifacts/candidate.py").write_text(Path("candidate.py").read_text())
print(json.dumps(metrics, indent=2), flush=True)
'''


def benchmark_files(round_number, history):
    # Fixed-budget, reproducible optimizer search. These are actual executions,
    # not seeded UI metrics or a simulated model response.
    rates = [0.0001, 0.00025, 0.0005, 0.0008, 0.0012, 0.0016, 0.002]
    rate = rates[(round_number - 1) % len(rates)]
    if round_number > len(rates) and history:
        best = max((j for j in history if "score" in j.metrics), key=lambda j: j.metrics["score"], default=None)
        if best:
            text = best.spec["files"].get("candidate.py", "")
            for line in text.splitlines():
                if line.startswith("RATE = "):
                    rate = float(line.split("=")[1]) * (0.9 if round_number % 2 else 1.1)
    code = f'''# Candidate {round_number}: fixed-budget gradient descent on Rosenbrock.
RATE = {rate!r}
def optimize(start, steps):
    x, y = start
    for _ in range(steps):
        gx = 2*(x-1) + 400*x*(x*x-y)
        gy = 200*(y-x*x)
        scale = max(1.0, abs(gx)/100, abs(gy)/100)
        x -= RATE*gx/scale
        y -= RATE*gy/scale
    return x, y
'''
    return {"candidate.py": code, "evaluate.py": EVALUATOR}


def validate_repository(settings, repo_path, command):
    path = Path(repo_path).expanduser().resolve()
    if not path.is_relative_to(settings.repo_root):
        raise HTTPException(422, f"Repository must be under CLUSTER_REPO_ROOT ({settings.repo_root})")
    try:
        result = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True, timeout=10)
        if Path(result.stdout.strip()).resolve() != path:
            raise ValueError("Provide the repository root, not a subdirectory")
        commit = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        protected = []
        for arg in command:
            candidate = (path / arg).resolve()
            if candidate.is_relative_to(path) and candidate.is_file():
                protected.append(candidate.relative_to(path).as_posix())
        if not protected:
            raise ValueError("Evaluation command must reference a versioned evaluator file, e.g. python evaluate.py")
        for name in protected:
            subprocess.run(["git", "-C", str(path), "cat-file", "-e", f"{commit}:{name}"], capture_output=True, check=True, timeout=10)
        return {"repo_path": str(path), "base_commit": commit, "protected_files": protected}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise HTTPException(422, "需要有效 Git 仓库及已提交的评测脚本：" + str(error)) from error


def repository_files(workspace):
    paths = subprocess.run(["git", "-C", str(workspace), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, check=True, timeout=20).stdout.decode().split("\0")
    files, total = {}, 0
    for name in paths:
        if not name or any(part in {".git", ".venv", "node_modules", "__pycache__", ".state"} for part in Path(name).parts):
            continue
        if Path(name).name.startswith(".env"):
            continue
        path = workspace / name
        if not path.is_file() or path.is_symlink():
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"Source bundle supports text files only; move binary data out of the repository: {name}") from error
        total += len(raw)
        if total > 8*1024*1024 or len(files) >= 2000:
            raise ValueError("Repository source bundle exceeds 8 MiB / 2000 files; use a focused experiment repository")
        files[name] = text
    return files


def propose_repository(db, settings, study, history, round_number):
    cfg = study.config
    workspace = settings.data_dir / "worktrees" / study.id / f"round-{round_number}-{uid()[:8]}"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--local", "--no-hardlinks", "--no-checkout", cfg["repo_path"], str(workspace)],
                   check=True, capture_output=True, timeout=60)
    subprocess.run(["git", "-C", str(workspace), "checkout", "--detach", cfg["base_commit"]], check=True, capture_output=True, timeout=30)
    parent = next((j for j in history if j.id == study.best_job_id), None)
    if parent:
        # Make the parent snapshot exact, including deletions since the baseline.
        tracked = subprocess.run(["git", "-C", str(workspace), "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
        for name in tracked:
            if name and name not in parent.spec["files"]:
                (workspace / name).unlink(missing_ok=True)
        for name, text in parent.spec["files"].items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    protected = {}
    for name in cfg["protected_files"]:
        protected[name] = subprocess.run(["git", "-C", cfg["repo_path"], "show", f"{cfg['base_commit']}:{name}"], capture_output=True, check=True).stdout.decode()
    summary = [{"round": j.round, "status": j.status, "score": j.metrics.get(cfg["metric"]), "error": j.error} for j in history]
    approved_task = cfg.get("literature", {}).get("selected_task", {})
    verification = cfg.get("verification", {"mode": "off"})
    prompt = (f"Research goal: {study.objective}\nRound: {round_number}/{study.rounds}.\n"
              f"Approved evidence-grounded task: {json.dumps(approved_task, ensure_ascii=False)}\n"
              f"History: {summary}\nImprove this repository with one testable, focused candidate. "
              f"Selection metric: {cfg['metric']} ({cfg['direction']}). "
              f"Evaluation command: {cfg['evaluation_command']}. It must write finite numeric metrics to metrics.json. "
              f"Do not modify these frozen evaluator files: {cfg['protected_files']}. "
              "Make code changes only. Heavy experiments are submitted separately to the compute backend. "
              "Record the hypothesis and changes in RESEARCH_NOTES.md. Do not access credentials or modify git metadata.")
    if verification["mode"] != "off":
        prompt += ("\nFormal verification is configured. Write a JSON mapping from target IDs to Lean proof terms at "
            + verification["proof_path"] + ". Do not change the frozen propositions or assume their conclusions. "
            + "Use core/Std proof terms; no sorry, custom axioms, native_decide, declarations or metaprogramming. "
            + json.dumps({"specification": verification["spec_source"], "targets": verification["targets"],
                          "coverage": verification["coverage"]}, ensure_ascii=False))
    prompt_path = workspace.parent / f"{workspace.name}.prompt.txt"
    prompt_path.write_text(prompt)
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TMPDIR", "SSL_CERT_FILE"} or k.startswith("LLM_")}
    env["CUDA_VISIBLE_DEVICES"] = ""
    process = subprocess.Popen([sys.executable, "-m", "research_cluster.agent_task", str(workspace), str(prompt_path),
                               str(cfg["agent_max_iterations"]), str(cfg["agent_budget_usd"])],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    with _agent_lock:
        _agent_processes.add(process)
    deadline = time.monotonic() + cfg["agent_timeout_seconds"]
    try:
        while True:
            try:
                _, stderr = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() > deadline:
                    raise TimeoutError("Agent 超过本轮时间预算")
                with db.transaction(write=False) as session:
                    current = session.get(Study, study.id)
                    if current is None or current.status == "canceled":
                        raise RuntimeError("研究已取消；停止候选生成")
    finally:
        cleanup_agent(process)
        with _agent_lock:
            _agent_processes.discard(process)
    if process.returncode:
        # Agent errors can include provider request details. Keep credentials out of persistent logs.
        message = stderr[-1500:]
        for key in ("LLM_API_KEY",):
            if os.getenv(key):
                message = message.replace(os.environ[key], "[redacted]")
        raise RuntimeError("OpenHands 执行失败：" + message)
    for name, text in protected.items():
        path = workspace / name
        if path.is_symlink():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return repository_files(workspace)


def research_step(db, settings, study_id):
    """One idempotent transition; waiting for compute never holds a worker."""
    with db.transaction(write=False) as session:
        snapshot = get_row(session, Study, study_id)
        needs_literature = snapshot.mode != "benchmark" and snapshot.config.get("literature", {}).get("phase") != "execution_ready"
    if needs_literature:
        from .literature import literature_step
        return literature_step(db, settings, study_id)
    from .verification import FINISHED, ensure_checks, verification_step
    verification_step(db, settings, study_id)
    with db.transaction() as s:
        reap_expired(s)
        study = get_row(s, Study, study_id, lock=True)
        if study.status in {"completed", "failed", "canceled"}:
            return True
        if study.status == "paused":
            return False
        history = list(s.scalars(select(Job).where(Job.study_id == study_id).order_by(Job.round)))
        checks = ensure_checks(s, study, history)
        required = study.config.get("verification", {}).get("mode") == "required"
        metric, direction = study.config["metric"], study.config["direction"]
        valid = [j for j in history if j.status == "succeeded" and metric in j.metrics
                 and (not required or (j.id in checks and checks[j.id].status == "passed"))]
        if valid:
            best = (max if direction == "max" else min)(valid, key=lambda j: j.metrics[metric])
            if study.best_job_id != best.id:
                study.best_job_id, study.best_score = best.id, best.metrics[metric]
                record(s, "candidate.promoted", f"候选归档 · {metric} = {study.best_score:.6g}", job=best)
        if any(j.status not in TERMINAL for j in history):
            return False
        if any(check.status not in FINISHED or (check.status in {"unproved", "error"} and settings.llm_ready
               and check.model_attempts < check.policy["repair_attempts"]) for check in checks.values()):
            return False
        if len(history) >= study.rounds:
            study.status = "completed" if valid else "failed"
            study.updated_at = now()
            record(s, "study.finished", "研究完成" if valid else "研究结束：没有取得有效评测结果", study=study)
            return True
        if study.planning_until > now():
            return False
        token, round_number = uid(), len(history) + 1
        study.planning_until = now() + study.config["agent_timeout_seconds"] + 120
        study.planning_token = token
        record(s, "candidate.planning", f"正在生成第 {round_number} 个候选方案", study=study)
    try:
        if study.config.get("infrastructure_smoke") == "lean":
            from .verification import smoke_files
            files = smoke_files()
        else:
            files = benchmark_files(round_number, history) if study.mode == "benchmark" else propose_repository(db, settings, study, history, round_number)
        cfg = study.config
        value = JobInput(name=f"{study.name[:130]} · #{round_number:02}", kind=cfg["kind"], pool_id=cfg["pool_id"],
                         resources=cfg["resources"], command=["python", "evaluate.py"] if study.mode == "benchmark" else cfg["evaluation_command"],
                         timeout_seconds=cfg["timeout_seconds"], files=files, idempotency_key=f"study:{study.id}:round:{round_number}")
        with db.transaction() as s:
            current = get_row(s, Study, study_id, lock=True)
            if current.planning_token != token:
                return False
            current.planning_until, current.planning_token = 0, None
            if current.status in {"active", "paused"}:
                create_job(s, value, study=current, round_number=round_number, parent_id=current.best_job_id)
                current.updated_at = now()
    except Exception as error:
        log.exception("Candidate generation failed for %s", study_id)
        with db.transaction() as s:
            current = get_row(s, Study, study_id, lock=True)
            if current.planning_token == token:
                current.status, current.planning_until, current.planning_token = "failed", 0, None
                record(s, "study.error", str(error)[:2000], study=current)
    return False


def run_local_orchestrator(settings, stop=None):
    from concurrent.futures import ThreadPoolExecutor
    from .database import Database
    stop = stop or threading.Event()
    db = Database(settings.database_url)
    pending = {}
    last_scheduled = None
    with ThreadPoolExecutor(max_workers=settings.max_concurrent_research, thread_name_prefix="research") as executor:
        try:
            while not stop.is_set():
                pending = {k: f for k, f in pending.items() if not f.done()}
                with db.transaction() as s:
                    reap_expired(s)
                    identities = list(s.scalars(select(Study.id).where(Study.status == "active")
                                               .order_by(Study.created_at, Study.id)))
                # A waiting review or experiment can remain active indefinitely.
                # Rotate instead of always filling the pool with the first IDs.
                if last_scheduled in identities:
                    offset = identities.index(last_scheduled) + 1
                    identities = identities[offset:] + identities[:offset]
                for identity in identities:
                    if identity not in pending and len(pending) < settings.max_concurrent_research:
                        pending[identity] = executor.submit(research_step, db, settings, identity)
                        last_scheduled = identity
                stop.wait(1)
        finally:
            stop_agent_processes()
    db.engine.dispose()
