import math
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Resources(BaseModel):
    cpus: int = Field(default=1, ge=1, le=1024)
    memory_mb: int = Field(default=512, ge=64, le=16777216)
    gpus: int = Field(default=0, ge=0, le=256)


class JobInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["cpu", "gpu"] = "cpu"
    pool_id: str = "cpu-default"
    command: list[str] = Field(default_factory=lambda: ["python", "main.py"], min_length=1, max_length=100)
    files: dict[str, str] = Field(default_factory=dict)
    resources: Resources = Field(default_factory=Resources)
    timeout_seconds: int = Field(default=300, ge=1, le=604800)
    max_attempts: int = Field(default=2, ge=1, le=5)
    idempotency_key: str | None = Field(default=None, max_length=180)

    @model_validator(mode="after")
    def valid(self):
        if self.kind == "cpu" and self.resources.gpus:
            raise ValueError("CPU jobs cannot request GPUs")
        if self.kind == "gpu" and not self.resources.gpus:
            raise ValueError("GPU jobs must request at least one GPU")
        if any(not part or "\x00" in part for part in self.command):
            raise ValueError("Command must contain nonempty argv strings")
        total = 0
        for name, content in self.files.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or "\x00" in name or not name or name.endswith("/"):
                raise ValueError("File paths must be relative and cannot traverse directories")
            total += len(content.encode())
        if len(self.files) > 2000 or total > 8 * 1024 * 1024:
            raise ValueError("Source bundle exceeds 8 MiB or 2000 files")
        return self


class LeanTarget(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    proposition: str = Field(pattern=r"^ClusterSpec\.[A-Za-z][A-Za-z0-9_.]{0,150}$")
    description: str = Field(default="", max_length=1000)


class LeanPolicy(BaseModel):
    mode: Literal["off", "advisory", "required"] = "off"
    pool_id: str = "lean-cpu"
    toolchain: Literal["4.35.0"] = "4.35.0"
    spec_source: str = Field(default="", max_length=64000)
    proof_path: str = "formal/proofs.json"
    targets: list[LeanTarget] = Field(default_factory=list, max_length=20)
    coverage: str = Field(default="仅覆盖登记的 Lean 数学规格；实际实现与规格的对应关系仍需审核。", max_length=3000)
    timeout_seconds: int = Field(default=120, ge=5, le=1800)
    resources: Resources = Field(default_factory=lambda: Resources(cpus=1, memory_mb=1024))
    repair_attempts: int = Field(default=1, ge=0, le=4)
    repair_max_tokens: int = Field(default=2048, ge=128, le=8000)
    repair_timeout_seconds: int = Field(default=60, ge=5, le=180)

    @model_validator(mode="after")
    def valid(self):
        JobInput(name="Lean verification", files={self.proof_path: ""}, resources=self.resources)
        if not self.proof_path.endswith(".json"):
            raise ValueError("Lean proof_path must be a relative JSON file")
        if self.resources.gpus:
            raise ValueError("Lean verification requires an independent CPU pool")
        if self.mode != "off" and (not self.spec_source.strip() or not self.targets):
            raise ValueError("启用 Lean 时需要冻结规格和至少一个证明目标")
        if len({item.id for item in self.targets}) != len(self.targets):
            raise ValueError("Lean target IDs must be unique")
        return self


class FormalAssessment(BaseModel):
    status: Literal["unassessed", "not_applicable", "recommended", "required"] = "unassessed"
    rationale: str = Field(default="实施前评估数学性质和关键不变量是否需要证明。", max_length=3000)
    obligations: list[str] = Field(default_factory=list, max_length=20)


class StudyInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=20000)
    domain: Literal["ml", "finance", "general"] = "ml"
    mode: Literal["literature", "benchmark", "openhands"] = "literature"
    query_terms: list[str] = Field(default_factory=list, max_length=8)
    lookback_days: int = Field(default=90, ge=7, le=730)
    paper_limit: int = Field(default=20, ge=3, le=40)
    baseline_papers: list[str] = Field(default_factory=list, max_length=12)
    known_context: str = Field(default="", max_length=24000)
    context_paths: list[str] = Field(default_factory=list, max_length=8)
    rounds: int = Field(default=5, ge=1, le=100)
    kind: Literal["cpu", "gpu"] = "cpu"
    pool_id: str = "cpu-default"
    resources: Resources = Field(default_factory=Resources)
    repo_path: str = ""
    evaluation_command: list[str] = Field(default_factory=lambda: ["python", "evaluate.py"])
    metric: str = Field(default="score", min_length=1, max_length=100)
    direction: Literal["max", "min"] = "max"
    timeout_seconds: int = Field(default=300, ge=1, le=604800)
    agent_timeout_seconds: int = Field(default=600, ge=10, le=3600)
    agent_max_iterations: int = Field(default=30, ge=1, le=200)
    agent_budget_usd: float = Field(default=2.0, ge=0.01, le=1000, allow_inf_nan=False)
    verification: LeanPolicy = Field(default_factory=LeanPolicy)

    @model_validator(mode="after")
    def valid(self):
        JobInput(name=self.name, kind=self.kind, pool_id=self.pool_id, resources=self.resources, command=self.evaluation_command)
        if self.mode == "benchmark" and self.kind != "cpu":
            raise ValueError("The built-in benchmark runs on CPU; use a repository experiment for GPU jobs")
        if self.mode == "benchmark" and (self.metric != "score" or self.direction != "max"):
            raise ValueError("The built-in benchmark selects score in max direction")
        if any(len(term) > 120 or not term.strip() for term in self.query_terms):
            raise ValueError("Search terms must be nonempty and at most 120 characters")
        if any(not __import__("re").fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", identity) for identity in self.baseline_papers):
            raise ValueError("Baseline papers require valid arXiv IDs")
        if any(PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path for path in self.context_paths):
            raise ValueError("Local evidence files must be relative paths inside the reference repository")
        return self


class ProposalBudget(BaseModel):
    cpus: int = Field(default=2, ge=1, le=1024)
    gpus: int = Field(default=1, ge=0, le=256)
    gpu_hours: float = Field(default=1, ge=0, le=10000, allow_inf_nan=False)
    note: str = Field(default="预估上限；实施前用小实验校准", max_length=500)


class ResearchProposal(BaseModel):
    id: str = Field(default="", max_length=80)
    title: str = Field(min_length=1, max_length=180)
    priority: Literal["P0", "P1", "P2"] = "P1"
    hypothesis: str = Field(min_length=10, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    rationale: str = Field(min_length=10, max_length=4000)
    baseline: str = Field(min_length=5, max_length=4000)
    experiments: list[str] = Field(min_length=1, max_length=12)
    metrics: list[str] = Field(min_length=1, max_length=20)
    acceptance: list[str] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)
    budget: ProposalBudget = Field(default_factory=ProposalBudget)
    status: Literal["draft", "accepted", "deferred"] = "draft"
    execution_study_id: str | None = None
    formal_verification: FormalAssessment = Field(default_factory=FormalAssessment)


class LiteratureSynthesis(BaseModel):
    summary: str = Field(min_length=10, max_length=10000)
    tasks: list[ResearchProposal] = Field(min_length=1, max_length=8)
    method: Literal["assistant_reviewed", "model", "evidence_checklist"] = "assistant_reviewed"


class PoolInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["cpu", "gpu"]


class GPUInfo(BaseModel):
    index: str = Field(min_length=1, max_length=20)
    uuid: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    memory_mb: int | None = Field(default=None, ge=0)


class InventoryInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    host_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    cpu_scope: str = Field(default="", max_length=80)
    hostname: str = Field(min_length=1, max_length=160)
    cpu_logical: int = Field(ge=1, le=1048576)
    cpu_physical: int | None = Field(default=None, ge=1, le=1048576)
    cpu_affinity: int = Field(ge=1, le=1048576)
    cpu_quota: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    cpu_effective: float = Field(gt=0, allow_inf_nan=False)
    gpus: list[GPUInfo] = Field(default_factory=list, max_length=256)
    gpu_status: Literal["ok", "restricted", "unavailable", "error"]
    gpu_error: str | None = Field(default=None, max_length=500)


class WorkerInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=160)
    pool_id: str
    kind: Literal["cpu", "gpu"]
    cpus: int = Field(ge=1, le=1024)
    memory_mb: int = Field(ge=64)
    gpu_ids: list[str] = Field(default_factory=list, max_length=256)
    concurrency: int = Field(default=1, ge=1, le=256)
    inventory: InventoryInput | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.kind == "cpu" and self.gpu_ids:
            raise ValueError("CPU workers cannot register GPU devices")
        if self.kind == "gpu" and not self.gpu_ids:
            raise ValueError("GPU workers require explicit device IDs")
        if len(set(self.gpu_ids)) != len(self.gpu_ids) or any(not x or not all(c.isalnum() or c in "-_." for c in x) for x in self.gpu_ids):
            raise ValueError("GPU IDs must be unique CUDA device IDs or UUIDs")
        return self


class HeartbeatInput(BaseModel):
    lease_token: str
    logs: str = Field(default="", max_length=65536)
    offset: int = Field(default=0, ge=0)


class FinishInput(HeartbeatInput):
    status: Literal["succeeded", "failed", "canceled"]
    metrics: dict[str, float] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=4000)
    proof_report: dict | None = None

    @model_validator(mode="after")
    def valid_metrics(self):
        if len(self.metrics) > 100 or any(not math.isfinite(x) for x in self.metrics.values()):
            raise ValueError("Metrics must contain at most 100 finite numbers")
        if self.proof_report is not None and len(__import__("json").dumps(self.proof_report)) > 100000:
            raise ValueError("Proof report exceeds 100 KiB")
        return self
