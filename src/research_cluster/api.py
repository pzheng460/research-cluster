import asyncio
from contextlib import asynccontextmanager
import hmac
import importlib.util
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .bundles import MAX_BUNDLE, pack_files, unpack_files
from .config import Settings
from .database import Database
from .models import Event, Inventory, Job, Pool, Study, Verification, Worker, now
from .schemas import FinishInput, FormalAssessment, HeartbeatInput, InventoryInput, JobInput, LiteratureSynthesis, PoolInput, StudyInput, WorkerInput
from .inventory import save_inventory, summarize
from .service import (RUNNING, TERMINAL, append_logs, authenticate_worker, cancel_job, check_lease,
                      claim_job, create_job, get_row, public, reap_expired, record, register_worker, validate_pool)
from .verification import cancel_checks, ensure_checks, finish_check, queue_check, validate_policy


def create_app(settings=None):
    settings = settings or Settings()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        db.engine.dispose()

    app = FastAPI(title="Research Cluster", version="0.1.0", lifespan=lifespan)
    app.state.db, app.state.settings = db, settings

    def authorize(authorization: str = Header(default="")):
        if settings.token and not hmac.compare_digest(authorization, f"Bearer {settings.token}"):
            raise HTTPException(401, "请先连接管理台并填写访问令牌")

    def trusted_origin(request: Request):
        # Prevent a browser on an unrelated website from submitting local commands.
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host"):
                raise HTTPException(403, "Cross-origin requests are not allowed")

    from fastapi import APIRouter
    router = APIRouter(prefix="/api", dependencies=[Depends(authorize), Depends(trusted_origin)])

    @router.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "orchestrator": settings.orchestrator,
                "authentication": bool(settings.token), "llm_configured": settings.llm_ready,
                "openhands_installed": importlib.util.find_spec("openhands") is not None,
                "database": db.engine.dialect.name, "research_default": "literature_first",
                "research_concurrency_per_orchestrator": settings.max_concurrent_research,
                "formal_verification": "lean", "lean_toolchain": "4.35.0"}

    @router.get("/state")
    def state():
        with db.transaction() as s:
            reap_expired(s)
            return {
                "studies": [public(x) for x in s.scalars(select(Study).order_by(Study.created_at.desc()).limit(500))],
                "jobs": [public(x) for x in s.scalars(select(Job).order_by(Job.created_at.desc()).limit(1000))],
                "pools": [public(x) for x in s.scalars(select(Pool).order_by(Pool.kind, Pool.created_at))],
                "workers": [public(x) for x in s.scalars(select(Worker).order_by(Worker.name))],
                "verifications": [public(x) for x in s.scalars(select(Verification).order_by(Verification.created_at.desc()).limit(1000))],
                "hardware": summarize(s.scalars(select(Inventory))),
                "events": [public(x) for x in s.scalars(select(Event).order_by(Event.id.desc()).limit(100))],
                "server_time": now(),
            }

    @router.post("/inventory")
    def inventory_report(value: InventoryInput):
        with db.transaction() as s:
            save_inventory(s, value)
        return {"status": "ok"}

    @router.post("/verification/smoke", status_code=201)
    def verification_smoke():
        from .verification import smoke_policy
        value = StudyInput(name="Lean 形式验证链路自检（CPU）", objective="验证 CPU 样例、冻结命题、证明核验与晋级门槛；不代表实际金融系统已验证。",
                           mode="benchmark", rounds=1, verification=smoke_policy())
        with db.transaction() as s:
            study = Study(name=value.name, objective=value.objective, mode="benchmark", rounds=1,
                          config={**value.model_dump(), "infrastructure_smoke": "lean"})
            s.add(study)
            s.flush()
            record(s, "study.created", "Lean 形式验证基础设施自检已创建", study=study)
            return public(study)

    @router.post("/pools", status_code=201)
    def pool_create(value: PoolInput):
        with db.transaction() as s:
            if s.get(Pool, value.id):
                raise HTTPException(409, "Pool ID already exists")
            pool = Pool(**value.model_dump())
            s.add(pool)
            s.flush()
            record(s, "pool.created", f"已创建 {value.kind.upper()} 资源池：{value.name}")
            return public(pool)

    @router.post("/pools/{identity}/toggle")
    def pool_toggle(identity: str):
        with db.transaction() as s:
            pool = get_row(s, Pool, identity, lock=True)
            pool.enabled = not pool.enabled
            record(s, "pool.updated", f"{pool.name} {'恢复调度' if pool.enabled else '暂停调度'}")
            return public(pool)

    @router.post("/studies", status_code=201)
    def study_create(value: StudyInput):
        if value.repo_path:
            path = Path(value.repo_path).expanduser().resolve()
            if not path.is_dir() or not path.is_relative_to(settings.repo_root):
                raise HTTPException(422, "参考仓库必须位于 CLUSTER_REPO_ROOT 下")
        workflow = {} if value.mode == "benchmark" else {"workflow": "literature_first", "literature": {"phase": "queued", "papers": [], "tasks": []}}
        with db.transaction() as s:
            validate_pool(s, value.pool_id, value.kind)
            validate_policy(s, value.verification.model_dump())
            study = Study(name=value.name, objective=value.objective, domain=value.domain, mode=value.mode,
                          rounds=value.rounds, config={**value.model_dump(), **workflow})
            s.add(study)
            s.flush()
            record(s, "study.created", f"研究已创建：{value.name}", study=study)
            return public(study)

    @router.get("/studies/{identity}")
    def study_detail(identity: str):
        with db.transaction(write=False) as s:
            return public(get_row(s, Study, identity))

    @router.get("/studies/{identity}/tree")
    def study_tree(identity: str):
        from .tree import build_tree
        with db.transaction(write=False) as s:
            return build_tree(s, identity, settings)

    @router.post("/studies/{identity}/literature/refresh")
    def refresh_literature(identity: str):
        with db.transaction() as s:
            study = get_row(s, Study, identity, lock=True)
            previous = study.config.get("literature", {})
            if study.mode == "benchmark" or previous.get("phase") == "execution_ready" or study.status in {"completed", "failed", "canceled"}:
                raise HTTPException(409, "当前研究不能重新执行文献调研")
            study.planning_token, study.planning_until = None, 0
            study.config = {**study.config, "literature": {**previous, "phase": "queued", "papers": [], "error": None}}
            record(s, "literature.refresh", "已排队重新检索最新论文并综合候选任务", study=study)
            return public(study)

    @router.put("/studies/{identity}/synthesis")
    def reviewed_synthesis(identity: str, value: LiteratureSynthesis):
        from .literature import persist_synthesis
        with db.transaction() as s:
            study = get_row(s, Study, identity, lock=True)
            if study.mode == "benchmark" or study.config.get("literature", {}).get("phase") == "execution_ready" or study.status in {"completed", "canceled", "failed"}:
                raise HTTPException(409, "研究不可更新综合提案")
            try:
                # API imports are reviewed work, never impersonate a model run.
                value.method = "assistant_reviewed"
                persist_synthesis(study, value)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            study.planning_token, study.planning_until = None, 0
            record(s, "literature.reviewed", f"已导入 {len(value.tasks)} 项有文献依据的综合研究任务", study=study)
            return public(study)

    @router.post("/studies/{identity}/proposals/{task_id}/{action}")
    def proposal_action(identity: str, task_id: str, action: str):
        if action not in {"accept", "defer", "restore"}:
            raise HTTPException(404, "Unknown proposal action")
        with db.transaction() as s:
            study = get_row(s, Study, identity, lock=True)
            literature = dict(study.config.get("literature", {}))
            tasks = [dict(task) for task in literature.get("tasks", [])]
            task = next((task for task in tasks if task["id"] == task_id), None)
            if not task:
                raise HTTPException(404, "Proposal not found")
            if task.get("execution_study_id"):
                raise HTTPException(409, "该任务已有执行研究，请管理其执行状态")
            task["status"] = {"accept": "accepted", "defer": "deferred", "restore": "draft"}[action]
            study.config = {**study.config, "literature": {**literature, "tasks": tasks}}
            record(s, "proposal." + action, f"任务状态更新：{task['title']}", study=study)
            return public(study)

    @router.put("/studies/{identity}/proposals/{task_id}/execution", status_code=201)
    def proposal_execute(identity: str, task_id: str, value: StudyInput):
        if not settings.llm_ready or importlib.util.find_spec("openhands") is None:
            raise HTTPException(422, "实验阶段需要配置模型 API 并安装 OpenHands 依赖")
        from .research import validate_repository
        repository = validate_repository(settings, value.repo_path, value.evaluation_command)
        with db.transaction() as s:
            parent = get_row(s, Study, identity, lock=True)
            literature = dict(parent.config.get("literature", {}))
            tasks = [dict(task) for task in literature.get("tasks", [])]
            task = next((task for task in tasks if task["id"] == task_id), None)
            if not task or task.get("status") != "accepted":
                raise HTTPException(422, "请先采纳候选研究任务")
            if task.get("execution_study_id"):
                return public(get_row(s, Study, task["execution_study_id"]))
            validate_pool(s, value.pool_id, value.kind)
            validate_policy(s, value.verification.model_dump())
            if task.get("formal_verification", {}).get("status") == "required" and value.verification.mode != "required":
                raise HTTPException(422, "此提案要求形式验证；请冻结规格并选择 Lean 必须通过")
            child = Study(name=value.name, objective=value.objective, domain=value.domain, mode="openhands", rounds=value.rounds,
                config={**value.model_dump(), **repository, "mode": "openhands", "workflow": "literature_first",
                    "parent_research_id": parent.id, "proposal_id": task_id,
                    "literature": {"phase": "execution_ready", "papers": literature.get("papers", []), "selected_task": task}})
            s.add(child)
            s.flush()
            task["execution_study_id"] = child.id
            parent.config = {**parent.config, "literature": {**literature, "tasks": tasks}}
            record(s, "proposal.execution", f"已为采纳任务创建实验研究：{child.name}", study=child)
            return public(child)

    @router.put("/studies/{identity}/proposals/{task_id}/formal-verification")
    def proposal_formal_assessment(identity: str, task_id: str, value: FormalAssessment):
        with db.transaction() as s:
            study = get_row(s, Study, identity, lock=True)
            literature = study.config.get("literature", {})
            tasks = [dict(item) for item in literature.get("tasks", [])]
            task = next((item for item in tasks if item["id"] == task_id), None)
            if not task or task.get("execution_study_id") or study.status in {"completed", "failed", "canceled"}:
                raise HTTPException(409, "只能评估尚未启动执行的研究提案")
            task["formal_verification"] = value.model_dump()
            study.config = {**study.config, "literature": {**literature, "tasks": tasks}}
            record(s, "proposal.formal_assessment", "已更新提案的证明义务评估", study=study)
            return public(study)

    @router.post("/studies/{identity}/{action}")
    def study_action(identity: str, action: str):
        if action not in {"pause", "resume", "cancel"}:
            raise HTTPException(404, "Unknown study action")
        with db.transaction() as s:
            study = get_row(s, Study, identity, lock=True)
            if study.status in {"completed", "canceled", "failed"}:
                raise HTTPException(409, "研究已结束，请创建新的研究")
            study.status = {"pause": "paused", "resume": "active", "cancel": "canceled"}[action]
            study.updated_at = now()
            if action == "cancel":
                study.planning_token, study.planning_until = None, 0
                for job in s.scalars(select(Job).where(Job.study_id == identity)):
                    cancel_job(s, job)
                cancel_checks(s, identity)
            record(s, f"study.{action}", {"pause": "研究已暂停；已有实验继续运行", "resume": "研究已恢复", "cancel": "研究已取消"}[action], study=study)
            return public(study)

    @router.post("/jobs", status_code=201)
    def job_create(value: JobInput):
        with db.transaction() as s:
            return public(create_job(s, value))

    @router.get("/jobs/{identity}")
    def job_detail(identity: str):
        with db.transaction(write=False) as s:
            return public(get_row(s, Job, identity), detail=True)

    @router.get("/jobs/{identity}/source")
    def source_download(identity: str):
        with db.transaction(write=False) as s:
            job = get_row(s, Job, identity)
            data = pack_files(job.spec["files"])
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="candidate-{identity[:12]}.zip"'})

    @router.get("/jobs/{identity}/verification")
    def verification_detail(identity: str):
        with db.transaction(write=False) as s:
            get_row(s, Job, identity)
            check = s.scalar(select(Verification).where(Verification.candidate_id == identity))
            return {"verification": public(check) if check else None,
                    "checks": [public(s.get(Job, job_id)) for job_id in check.job_ids] if check else []}

    @router.post("/jobs/{identity}/verification/retry", status_code=201)
    def verification_retry(identity: str):
        with db.transaction() as s:
            check = s.scalar(select(Verification).where(Verification.candidate_id == identity).with_for_update())
            if not check or check.status not in {"unproved", "error", "canceled"}:
                raise HTTPException(409, "只有未完成的证明可以重新验证")
            if get_row(s, Study, check.study_id).status in {"completed", "failed", "canceled"}:
                raise HTTPException(409, "研究已结束，请创建新的研究")
            if len(check.job_ids) >= 20:
                raise HTTPException(409, "该候选已达到人工重检次数上限")
            return public(queue_check(s, check))

    @router.post("/jobs/{identity}/cancel")
    def job_cancel(identity: str):
        with db.transaction() as s:
            job = get_row(s, Job, identity, lock=True)
            cancel_job(s, job)
            return public(job)

    @router.post("/jobs/{identity}/retry", status_code=201)
    def job_retry(identity: str):
        with db.transaction() as s:
            job = get_row(s, Job, identity)
            if job.spec.get("verification"):
                raise HTTPException(409, "请通过候选的形式验证重试接口重检")
            if job.status not in {"failed", "canceled"}:
                raise HTTPException(409, "Only failed or canceled jobs can be retried")
            data = {k: v for k, v in job.spec.items() if k != "assigned_gpu_ids"}
            data["name"] = job.name[:145] + " · 重试"
            # A retry is a new candidate. Preserve the original immutable run.
            retry = create_job(s, JobInput(**data), parent_id=job.id)
            original_check = s.scalar(select(Verification).where(Verification.candidate_id == job.id))
            owner = job.study_id or (original_check.study_id if original_check else None)
            if owner:
                ensure_checks(s, get_row(s, Study, owner), [retry])
            return public(retry)

    @router.post("/workers/register", status_code=201)
    def worker_register(value: WorkerInput):
        with db.transaction() as s:
            return {"worker_token": register_worker(s, value), "lease_seconds": settings.lease_seconds}

    @router.post("/workers/{identity}/claim")
    def worker_claim(identity: str, x_worker_token: str = Header(default="")):
        with db.transaction() as s:
            worker = authenticate_worker(s, identity, x_worker_token, lock=True)
            reap_expired(s)
            job = claim_job(s, worker, settings.lease_seconds)
            if job and job["spec"].get("verification"):
                check = s.get(Verification, job["spec"]["verification"]["verification_id"])
                check.status, check.updated_at = "running", now()
            return {"job": job}

    @router.post("/workers/{identity}/offline")
    def worker_offline(identity: str, x_worker_token: str = Header(default="")):
        with db.transaction() as s:
            worker = authenticate_worker(s, identity, x_worker_token, lock=True)
            worker.last_seen = 0
            record(s, "worker.offline", f"{worker.name} 已离线")
        return {"status": "ok"}

    @router.get("/jobs/{identity}/bundle")
    def bundle(identity: str, x_lease_token: str = Header(default="")):
        with db.transaction() as s:
            job = check_lease(s, identity, x_lease_token)
            data = pack_files(job.spec["files"])
        return Response(data, media_type="application/zip")

    @router.post("/jobs/{identity}/heartbeat")
    def heartbeat(identity: str, value: HeartbeatInput):
        with db.transaction() as s:
            job = check_lease(s, identity, value.lease_token)
            append_logs(job, value.offset, value.logs)
            job.lease_until = now() + settings.lease_seconds
            return {"cancel": job.status == "cancel_requested", "offset": job.log_offset}

    @router.post("/jobs/{identity}/artifacts")
    async def upload_artifacts(identity: str, request: Request, x_lease_token: str = Header(default="")):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BUNDLE:
                raise HTTPException(413, "Artifact archive exceeds 20 MiB")
            chunks.append(chunk)
        with db.transaction() as s:
            job = check_lease(s, identity, x_lease_token)
            target = settings.data_dir / "artifacts" / identity / str(job.attempt)
            try:
                artifacts = unpack_files(b"".join(chunks), target)
            except (ValueError, OSError, __import__("zipfile").BadZipFile) as error:
                raise HTTPException(422, str(error)) from error
            job.artifacts = artifacts
        return {"artifacts": artifacts}

    @router.get("/jobs/{identity}/artifacts/{name:path}")
    def download_artifact(identity: str, name: str):
        with db.transaction(write=False) as s:
            job = get_row(s, Job, identity)
            if name not in {x["name"] for x in job.artifacts}:
                raise HTTPException(404, "Artifact not found")
            path = settings.data_dir / "artifacts" / identity / str(job.attempt) / name
        return FileResponse(path, filename=Path(name).name)

    @router.post("/jobs/{identity}/finish")
    def finish(identity: str, value: FinishInput):
        with db.transaction() as s:
            job = check_lease(s, identity, value.lease_token, terminal_ok=True)
            if job.status in TERMINAL:
                return public(job)
            append_logs(job, value.offset, value.logs)
            job.status = "canceled" if job.status == "cancel_requested" else value.status
            job.metrics, job.error, job.finished_at = value.metrics, value.error, now()
            finish_check(s, job, value.proof_report)
            record(s, f"job.{job.status}", f"实验{'完成' if job.status == 'succeeded' else '失败' if job.status == 'failed' else '取消'}：{job.name}", job=job)
            return public(job)

    app.include_router(router)
    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
