from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc).timestamp()


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Pool(Base):
    __tablename__ = "pools"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(8))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    pool_id: Mapped[str] = mapped_column(ForeignKey("pools.id"))
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(8))
    cpus: Mapped[int] = mapped_column(Integer)
    memory_mb: Mapped[int] = mapped_column(Integer)
    gpu_ids: Mapped[list] = mapped_column(JSON, default=list)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    secret_hash: Mapped[str] = mapped_column(String(64))
    last_seen: Mapped[float] = mapped_column(Float, default=now)


class Inventory(Base):
    __tablename__ = "hardware_inventory"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(80), index=True)
    hostname: Mapped[str] = mapped_column(String(160))
    details: Mapped[dict] = mapped_column(JSON)
    last_seen: Mapped[float] = mapped_column(Float, default=now)


class Study(Base):
    __tablename__ = "studies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160))
    objective: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(20), default="ml")
    mode: Mapped[str] = mapped_column(String(20), default="literature")
    status: Mapped[str] = mapped_column(String(20), default="active")
    rounds: Mapped[int] = mapped_column(Integer, default=5)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    best_job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    best_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    planning_until: Mapped[float] = mapped_column(Float, default=0)
    planning_token: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    study_id: Mapped[str | None] = mapped_column(ForeignKey("studies.id"), nullable=True)
    round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(8))
    pool_id: Mapped[str] = mapped_column(ForeignKey("pools.id"))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    spec: Mapped[dict] = mapped_column(JSON)
    worker_id: Mapped[str | None] = mapped_column(ForeignKey("workers.id"), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    logs: Mapped[str] = mapped_column(Text, default="")
    log_offset: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Verification(Base):
    __tablename__ = "verifications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    policy: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64))
    spec_hash: Mapped[str] = mapped_column(String(64))
    job_ids: Mapped[list] = mapped_column(JSON, default=list)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    proofs: Mapped[str] = mapped_column(Text, default="")
    model_attempts: Mapped[int] = mapped_column(Integer, default=0)
    planning_token: Mapped[str | None] = mapped_column(String(40), nullable=True)
    planning_until: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=now)
