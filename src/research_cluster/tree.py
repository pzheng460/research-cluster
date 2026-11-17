"""A read-only execution tree derived from durable research records.

Stage nodes are logical roles, not invented model processes. Papers, proposals,
child studies, candidates, retries and measurements retain their actual IDs.
"""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import defer, load_only

from .models import Job, Study, Verification, Worker, now
from .service import get_row

def build_tree(session, root_id, settings):
    root = get_row(session, Study, root_id)
    timestamp = now()
    relationships = list(session.execute(select(Study.id, Study.config["parent_research_id"].as_string())))
    descendants = defaultdict(list)
    for identity, parent in relationships:
        if parent:
            descendants[parent].append(identity)
    scope, pending, warnings = set(), [root_id], []
    while pending and len(scope) < 256:
        identity = pending.pop()
        if identity in scope:
            warnings.append("研究父子记录存在重复或环，已避免重复展开")
            continue
        scope.add(identity)
        pending.extend(descendants.get(identity, []))
    if pending:
        warnings.append("本视图最多展开 256 个执行研究")
    studies = list(session.scalars(select(Study).where(Study.id.in_(scope)).order_by(Study.created_at)))
    study_map = {study.id: study for study in studies}
    options = (defer(Job.spec), defer(Job.logs))
    jobs = list(session.scalars(select(Job).where(Job.study_id.in_(scope)).options(*options).order_by(Job.created_at)))
    owners = {job.id: job.study_id for job in jobs}
    frontier = [job.id for job in jobs]
    # Standalone retries preserve parent_id even if they have no study_id.
    while frontier and len(jobs) < 1500:
        more = list(session.scalars(select(Job).where(Job.study_id.is_(None), Job.parent_id.in_(frontier))
                                   .options(*options).order_by(Job.created_at)))
        fresh = [job for job in more if job.id not in owners]
        for job in fresh:
            owners[job.id] = owners[job.parent_id]
        jobs.extend(fresh)
        frontier = [job.id for job in fresh]
    job_map = {job.id: job for job in jobs}
    checks = {item.candidate_id: item for item in session.scalars(select(Verification).where(Verification.candidate_id.in_(job_map)))}
    check_ids = [identity for check in checks.values() for identity in check.job_ids]
    check_jobs = {job.id: job for job in session.scalars(select(Job).where(Job.id.in_(check_ids)).options(*options))} if check_ids else {}
    study_jobs = defaultdict(list)
    for job in jobs:
        study_jobs[owners[job.id]].append(job)
    workers = list(session.scalars(select(Worker).options(load_only(Worker.id, Worker.name, Worker.kind,
        Worker.last_seen, Worker.concurrency, Worker.cpus, Worker.gpu_ids))))
    worker_map = {worker.id: worker for worker in workers}
    online = [worker for worker in workers if timestamp - worker.last_seen < 40]
    nodes, edges, node_map = [], [], {}

    def add(identity, parent, kind, label, status, *, subtitle="", role=None, details=None, relation="contains"):
        if identity in node_map:
            return identity
        node = {"id": identity, "parent_id": parent, "kind": kind, "label": label, "status": status,
                "subtitle": subtitle, "role": role, "details": details or {}}
        nodes.append(node)
        node_map[identity] = node
        if parent:
            edges.append({"source": parent, "target": identity, "relation": relation})
        return identity

    def lease_state(study, idle="queued"):
        return "running" if study.planning_token and study.planning_until > timestamp else idle

    def job_state(job):
        if job.status in {"running", "cancel_requested"} and job.lease_until < timestamp:
            return "stale"
        return job.status

    added_studies = set()

    def emit_study(study, parent=None):
        if study.id in added_studies:
            return
        added_studies.add(study.id)
        identity = "study:" + study.id
        literature = study.config.get("literature", {})
        phase = literature.get("phase")
        status = phase if study.status == "active" and phase and phase != "execution_ready" else study.status
        owner = add(identity, parent, "research" if study.id == root_id else "execution", study.name, status,
            subtitle="研究目标" if study.id == root_id else "独立执行分支",
            role="coordinator", details={"study_id": study.id, "objective": study.objective, "mode": study.mode,
                "phase": phase, "best_score": study.best_score, "best_job_id": study.best_job_id,
                "created_at": study.created_at, "parent_research_id": study.config.get("parent_research_id")})
        proposals = {}
        if study.mode != "benchmark" and phase != "execution_ready":
            papers, tasks = literature.get("papers", []), literature.get("tasks", [])
            search_status = "completed" if papers else "error" if phase == "error" else lease_state(study) if phase == "collecting" else "queued"
            if study.status == "canceled" and not papers:
                search_status = "canceled"
            retrieval = add("retrieval:" + study.id, owner, "agent", "论文检索与证据收集", search_status,
                subtitle=f"{len(papers)} 篇论文 · 只读检索", role="retriever",
                details={"study_id": study.id, "model_backed": False, "query": literature.get("query"),
                    "coverage": literature.get("coverage"), "searched_at": literature.get("searched_at"),
                    "local_evidence_paths": [item["path"] for item in literature.get("local_context", [])]})
            group = add("papers:" + study.id, retrieval, "evidence", f"论文证据 · {len(papers)} 篇",
                "completed" if papers else "queued", subtitle="展开查看原始论文", details={"study_id": study.id, "count": len(papers)})
            for paper in papers:
                add(f"paper:{study.id}:{paper['id']}", group, "paper", paper["title"], "recorded",
                    subtitle=paper["id"] + " · " + paper.get("published", "")[:10], details={"study_id": study.id,
                        "paper_id": paper["id"], "url": paper["url"], "published": paper.get("published"),
                        "updated": paper.get("updated"), "read_depth": paper.get("read_depth", "abstract"),
                        "verified": paper.get("verified", False)})
            method = literature.get("method")
            method_label = {"model": "模型综合", "assistant_reviewed": "助手核验综合", "evidence_checklist": "规则核验草案"}.get(method, "待综合")
            synthesis_status = "completed" if tasks else "error" if phase == "error" and papers else lease_state(study) if phase == "synthesizing" else "queued"
            if study.status == "canceled" and not tasks:
                synthesis_status = "canceled"
            synthesis = add("synthesis:" + study.id, retrieval, "agent", "证据归纳与任务综合", synthesis_status,
                subtitle=method_label, role="synthesizer", details={"study_id": study.id, "method": method,
                    "model_backed": method == "model" or (phase == "synthesizing" and settings.llm_ready),
                    "summary": literature.get("summary", ""), "error": literature.get("error")}, relation="depends_on")
            for task in tasks:
                proposal = add(f"proposal:{study.id}:{task['id']}", synthesis, "proposal", task["title"], task.get("status", "draft"),
                    subtitle=f"{task.get('priority', 'P1')} · {len(task.get('evidence_ids', []))} 篇证据",
                    details={"study_id": study.id, "task_id": task["id"], "proposal": task}, relation="proposes")
                proposals[task["id"]] = proposal
        else:
            owned = study_jobs[study.id]
            smoke = study.config.get("infrastructure_smoke") == "lean"
            pending_job = any(job.status not in {"succeeded", "failed", "canceled"} for job in owned)
            stage_status = "completed" if study.status == "completed" else study.status if study.status in {"failed", "canceled"} else lease_state(study, "waiting" if pending_job else "queued")
            add("developer:" + study.id, owner, "agent", "CPU 自检准备器" if smoke else "参数搜索器（CPU 基准）" if study.mode == "benchmark" else "代码与候选生成 Agent",
                stage_status, subtitle="固定转账样例与证明输入" if smoke else "确定性参数搜索" if study.mode == "benchmark" else "OpenHands · 独立 Git 副本",
                role="smoke_setup" if smoke else "parameter_search" if study.mode == "benchmark" else "developer",
                details={"study_id": study.id, "model_backed": study.mode != "benchmark", "round_budget": study.rounds,
                    "completed_rounds": sum(job.status in {"succeeded", "failed", "canceled"} for job in owned),
                    "selected_task": literature.get("selected_task")})
        for child_id in descendants.get(study.id, []):
            child = study_map.get(child_id)
            if child and child.id not in added_studies:
                proposal = proposals.get(child.config.get("proposal_id"))
                if not proposal:
                    warnings.append(f"执行分支 {child.id[:8]} 未找到对应提案，显示在所属研究下")
                emit_study(child, proposal or owner)

    emit_study(root)

    def emit_job(job, path=None):
        identity = "candidate:" + job.id
        if identity in node_map:
            return identity
        path = set(path or ()) | {job.id}
        parent = "study:" + owners[job.id]
        previous = job_map.get(job.parent_id)
        relation = "runs"
        if previous and owners.get(previous.id) == owners[job.id]:
            if previous.id in path or len(path) >= 200:
                warnings.append("候选版本谱系存在环或层次过深，已断开循环引用")
            else:
                parent, relation = emit_job(previous, path), "derived_from"
        worker = worker_map.get(job.worker_id)
        state = job_state(job)
        label = f"候选 #{job.round:02d}" if job.round is not None else "重试候选"
        candidate = add(identity, parent, "candidate", label + " · " + job.name, state,
            subtitle=f"{job.kind.upper()} · {job.pool_id} · attempt {job.attempt}", role="executor",
            details={"study_id": owners[job.id], "job_id": job.id, "parent_job_id": job.parent_id,
                "worker_id": job.worker_id, "worker_name": worker.name if worker else None, "kind": job.kind,
                "pool_id": job.pool_id, "attempt": job.attempt, "metrics": job.metrics, "error": job.error,
                "started_at": job.started_at, "finished_at": job.finished_at,
                "best": study_map[owners[job.id]].best_job_id == job.id}, relation=relation)
        check = checks.get(job.id)
        if check:
            current = check_jobs.get(check.job_ids[-1]) if check.job_ids else None
            proof_status = check.status
            if proof_status in {"queued", "running"} and current:
                proof_status = job_state(current) if current.status not in {"failed", "succeeded"} else "error"
            proof = add("proof:" + check.id, candidate, "verification", "Lean 形式验证", proof_status,
                subtitle=("必须通过" if check.policy["mode"] == "required" else "附加验证") + " · CPU · Lean " + check.policy["toolchain"],
                role="lean_checker", details={"study_id": check.study_id, "candidate_id": job.id,
                    "verification_id": check.id, "job_ids": check.job_ids, "mode": check.policy["mode"],
                    "source_hash": check.source_hash, "spec_hash": check.spec_hash, "report": check.report,
                    "coverage": check.policy["coverage"], "pool_id": check.policy["pool_id"]}, relation="verified_by")
            target_reports = {item["id"]: item for item in check.report.get("targets", [])}
            for target in check.policy["targets"]:
                result = target_reports.get(target["id"], {})
                add(f"obligation:{check.id}:{target['id']}", proof, "obligation", target.get("description") or target["id"],
                    "passed" if check.status == "passed" else "unproved" if check.status in {"unproved", "error"} else "canceled" if check.status == "canceled" else "queued",
                    subtitle=target["proposition"], details={"study_id": check.study_id, "candidate_id": job.id,
                        "proposition": target["proposition"], "axioms": result.get("axioms", []),
                        "kernel_rechecked": check.report.get("kernel_rechecked", False)}, relation="proves")
            if check.model_attempts:
                add("prover:" + check.id, proof, "agent", "证明修复 Agent",
                    "running" if check.status == "repairing" and check.planning_until > timestamp else "failed" if check.status == "error" else "completed",
                    subtitle=f"已调用 {check.model_attempts} / {check.policy['repair_attempts']} 次", role="prover",
                    details={"study_id": check.study_id, "candidate_id": job.id, "model_backed": True,
                        "max_tokens_per_call": check.policy["repair_max_tokens"], "timeout_per_call": check.policy["repair_timeout_seconds"]})
        if job.status in {"succeeded", "failed", "canceled"}:
            metric = study_map[owners[job.id]].config.get("metric", "score")
            score = job.metrics.get(metric)
            result_status = job.status if job.status != "succeeded" or score is not None else "missing"
            add("result:" + job.id, candidate, "result", "评测结果" if score is not None else "执行结果 · 无有效主指标",
                result_status, subtitle=f"{metric} = {score:.6g}" if score is not None else job.status,
                role="evaluator", details={"study_id": owners[job.id], "job_id": job.id, "metrics": job.metrics,
                    "error": job.error, "best": study_map[owners[job.id]].best_job_id == job.id,
                    "artifacts": job.artifacts}, relation="measured_by")
        return candidate

    for job in jobs:
        if owners[job.id] in added_studies:
            emit_job(job)
    running_roles = [node for node in nodes if node["kind"] == "agent" and node["status"] == "running"]
    return {"version": 1, "root_id": "study:" + root_id, "nodes": nodes, "edges": edges,
        "generated_at": timestamp, "warnings": list(dict.fromkeys(warnings)),
        "summary": {"studies": len(added_studies), "proposals": sum(node["kind"] == "proposal" for node in nodes),
            "papers": sum(node["kind"] == "paper" for node in nodes), "candidates": len(jobs),
            "running_role_steps": len(running_roles),
            "running_model_steps": sum(node["details"].get("model_backed", False) for node in running_roles),
            "running_experiments": sum(job_state(job) in {"running", "cancel_requested"} for job in jobs)},
        "runtime": {"engine": settings.orchestrator, "model_ready": settings.llm_ready,
            "research_concurrency_per_orchestrator": settings.max_concurrent_research,
            "cpu_workers": sum(worker.kind == "cpu" for worker in online),
            "gpu_workers": sum(worker.kind == "gpu" for worker in online),
            "cpu_execution_slots": sum(worker.concurrency for worker in online if worker.kind == "cpu"),
            "gpu_execution_slots": sum(worker.concurrency for worker in online if worker.kind == "gpu")}}
