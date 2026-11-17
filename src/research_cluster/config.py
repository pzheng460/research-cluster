from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CLUSTER_DATA_DIR", ".state")).resolve())
    database_url: str = field(default_factory=lambda: os.getenv("CLUSTER_DATABASE_URL", ""))
    token: str = field(default_factory=lambda: os.getenv("CLUSTER_TOKEN", ""))
    orchestrator: str = field(default_factory=lambda: os.getenv("CLUSTER_ORCHESTRATOR", "local"))
    temporal_address: str = field(default_factory=lambda: os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    temporal_namespace: str = field(default_factory=lambda: os.getenv("TEMPORAL_NAMESPACE", "default"))
    repo_root: Path = field(default_factory=lambda: Path(os.getenv("CLUSTER_REPO_ROOT", ".")).resolve())
    max_concurrent_research: int = field(default_factory=lambda: int(os.getenv("CLUSTER_MAX_CONCURRENT_RESEARCH", "4")))
    lease_seconds: int = 30
    lean_home: Path | None = field(default_factory=lambda: Path(os.environ["CLUSTER_LEAN_HOME"]).resolve() if os.getenv("CLUSTER_LEAN_HOME") else None)

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.lean_home is None:
            self.lean_home = self.data_dir / "toolchains" / "lean-4.35.0-linux"
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'cluster.sqlite3'}"
        if self.orchestrator not in {"local", "temporal"}:
            raise ValueError("CLUSTER_ORCHESTRATOR must be local or temporal")
        if not 1 <= self.max_concurrent_research <= 32:
            raise ValueError("CLUSTER_MAX_CONCURRENT_RESEARCH must be between 1 and 32")

    @property
    def llm_ready(self):
        return bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL"))
