"""Transactional task broker. Resource queues are partitioned by pool and kind."""
import hashlib
import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import Event, Job, Pool, Study, Verification, Worker, now, uid

TERMINAL = {"succeeded", "failed", "canceled"}
RUNNING = {"running", "cancel_requested"}


def record(session, kind, message, job=None, study=None):
    session.add(Event(kind=kind, message=message, job_id=job.id if job else None,
                      study_id=study.id if study else (job.study_id if job else None)))


def public(row, detail=False):
    hidden = {"secret_hash", "lease_token", "planning_token", "planning_until"}
    result = {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in hidden}
    if isinstance(row, Job):
        result["spec"] = {k: v for k, v in row.spec.items() if k != "files"}
        if not detail:
            result.pop("logs", None)
    if isinstance(row, Worker):
        result["online"] = now() - row.last_seen < 40
    if isinstance(row, Verification):
        result.pop("proofs", None)
        result["policy"] = {key: value for key, value in row.policy.items() if key != "spec_source"}
    return result


def get_row(session, model, identity, lock=False):
    query = select(model).where(model.id == identity)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if not row:
        raise HTTPException(404, f"{model.__name__} not found")
    return row


def validate_pool(session, identity, kind):
    pool = get_row(session, Pool, identity)
    if pool.kind != kind:
        raise HTTPException(422, "Resource kind does not match pool")
    return pool


def create_job(session, value, *, study=None, round_number=None, parent_id=None):
    key = value.idempotency_key or uid()
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        supplied = value.model_dump(exclude={"idempotency_key"})
        original = {k: v for k, v in existing.spec.items() if k != "assigned_gpu_ids"}
        if original != supplied:
            raise HTTPException(409, "Idempotency key already belongs to a different job")
        return existing
    validate_pool(session, value.pool_id, value.kind)
    job = Job(id=uid(), idempotency_key=key, name=value.name, kind=value.kind,
              pool_id=value.pool_id, spec=value.model_dump(exclude={"idempotency_key"}),
              study_id=study.id if study else None, round=round_number, parent_id=parent_id)
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        # Concurrent Postgres submissions can race before either insert commits.
        existing = session.scalar(select(Job).where(Job.idempotency_key == key))
        if existing is None:
            raise
        original = {k: v for k, v in existing.spec.items() if k != "assigned_gpu_ids"}
        if original != value.model_dump(exclude={"idempotency_key"}):
            raise HTTPException(409, "Idempotency key already belongs to a different job")
        return existing
    record(session, "job.queued", f"实验已提交至 {value.pool_id}", job=job)
    return job


def register_worker(session, value):
    validate_pool(session, value.pool_id, value.kind)
    existing = session.get(Worker, value.id)
    if existing:
        # IDs describe a worker incarnation, never steal an active incarnation.
        if now() - existing.last_seen < 40 or session.scalar(select(Job).where(Job.worker_id == value.id, Job.status.in_(RUNNING))):
            raise HTTPException(409, "Worker ID is still active; use a new ID")
    secret = secrets.token_urlsafe(32)
    data = value.model_dump(exclude={"inventory"})
    if value.inventory:
        from .inventory import save_inventory
        save_inventory(session, value.inventory)
    if existing:
        for key, item in data.items():
            setattr(existing, key, item)
        existing.secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        existing.last_seen = now()
    else:
        session.add(Worker(**data, secret_hash=hashlib.sha256(secret.encode()).hexdigest()))
    record(session, "worker.online", f"{value.name} 加入 {value.pool_id}")
    return secret


def authenticate_worker(session, identity, secret, lock=False):
    worker = get_row(session, Worker, identity, lock=lock)
    expected = hashlib.sha256((secret or "").encode()).hexdigest()
    if not secrets.compare_digest(worker.secret_hash, expected):
        raise HTTPException(401, "Invalid worker credential")
    worker.last_seen = now()
    return worker


def claim_job(session, worker, lease_seconds):
    pool = get_row(session, Pool, worker.pool_id)
    if not pool.enabled:
        return None
    active = list(session.scalars(select(Job).where(Job.worker_id == worker.id, Job.status.in_(RUNNING))))
    if len(active) >= worker.concurrency:
        return None
    cpus = worker.cpus - sum(j.spec["resources"]["cpus"] for j in active)
    memory = worker.memory_mb - sum(j.spec["resources"]["memory_mb"] for j in active)
    assigned = {g for j in active for g in j.spec.get("assigned_gpu_ids", [])}
    free_gpus = [g for g in worker.gpu_ids if g not in assigned]
    candidates = session.scalars(select(Job).where(Job.pool_id == worker.pool_id, Job.kind == worker.kind,
                                Job.status == "queued",
                                Job.spec["resources"]["cpus"].as_integer() <= cpus,
                                Job.spec["resources"]["memory_mb"].as_integer() <= memory,
                                Job.spec["resources"]["gpus"].as_integer() <= len(free_gpus)
                                ).order_by(Job.created_at).with_for_update(skip_locked=True).limit(100))
    for job in candidates:
        resource = job.spec["resources"]
        if resource["cpus"] > cpus or resource["memory_mb"] > memory or resource["gpus"] > len(free_gpus):
            continue
        job.spec = {**job.spec, "assigned_gpu_ids": free_gpus[:resource["gpus"]]}
        job.status, job.worker_id = "running", worker.id
        job.lease_token = secrets.token_urlsafe(32)
        job.lease_until, job.started_at = now() + lease_seconds, now()
        job.attempt += 1
        job.logs, job.log_offset, job.error, job.metrics, job.artifacts = "", 0, None, {}, []
        record(session, "job.running", f"{worker.name} 开始执行 · 第 {job.attempt} 次尝试", job=job)
        return {**public(job, detail=True), "lease_token": job.lease_token,
                "lease_seconds": lease_seconds}
    return None


def check_lease(session, job_id, token, worker_id=None, terminal_ok=False):
    job = get_row(session, Job, job_id, lock=True)
    if not token or not secrets.compare_digest(job.lease_token or "", token):
        raise HTTPException(409, "Stale job lease")
    if worker_id and job.worker_id != worker_id:
        raise HTTPException(409, "Job belongs to a different worker")
    if job.status not in RUNNING:
        if terminal_ok and job.status in TERMINAL:
            return job
        raise HTTPException(409, "Job is no longer running")
    if job.lease_until < now():
        raise HTTPException(409, "Job lease expired")
    return job


def append_logs(job, offset, chunk):
    if offset > job.log_offset:
        raise HTTPException(409, f"Log offset mismatch: expected {job.log_offset}")
    overlap = job.log_offset - offset
    if overlap < len(chunk):
        fresh = chunk[overlap:]
        job.logs = (job.logs + fresh)[-1048576:]
        job.log_offset += len(fresh)


def cancel_job(session, job):
    if job.status == "queued":
        job.status, job.finished_at = "canceled", now()
    elif job.status == "running":
        job.status = "cancel_requested"
    else:
        return
    record(session, "job.cancel", "已请求取消实验", job=job)


def reap_expired(session):
    expired = session.scalars(select(Job).where(Job.status.in_(RUNNING), Job.lease_until < now() - 5).with_for_update(skip_locked=True))
    for job in expired:
        if job.status == "cancel_requested":
            job.status = "canceled"
            job.finished_at = now()
        elif job.attempt < job.spec["max_attempts"]:
            job.status = "queued"
            job.worker_id, job.lease_token = None, None
        else:
            job.status, job.finished_at = "failed", now()
        job.error = "执行节点租约过期；旧执行结果已隔离"
        record(session, "job.lease_expired", job.error, job=job)
