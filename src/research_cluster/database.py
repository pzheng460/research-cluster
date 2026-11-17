from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .models import Base, Pool


class Database:
    def __init__(self, url):
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}, pool_pre_ping=True)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure(dbapi, _):
                dbapi.execute("PRAGMA foreign_keys=ON")
                dbapi.execute("PRAGMA journal_mode=WAL")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        with self.transaction() as s:
            for kind in ("cpu", "gpu"):
                if not s.get(Pool, f"{kind}-default"):
                    s.add(Pool(id=f"{kind}-default", name=f"{kind.upper()} 默认资源池", kind=kind))
            if not s.get(Pool, "lean-cpu"):
                s.add(Pool(id="lean-cpu", name="Lean 形式验证 CPU 池", kind="cpu"))

    @contextmanager
    def transaction(self, write=True):
        with self.sessions() as session:
            try:
                # Serialize allocation on SQLite; Postgres uses row-level locks.
                if write and self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise
