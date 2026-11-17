"""Trusted CPU-side Lean runner. Candidate projects and Lake hooks are never run.

The frozen ClusterSpec module defines propositions. Candidates supply a JSON
mapping from target IDs to proof *terms*, not commands or project configuration.
The pinned compiler checks those exact propositions, followed by axiom auditing
and fresh kernel replay with the toolchain's leanchecker.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
FORBIDDEN = re.compile(r"\b(?:sorry|admit|sorryAx|axiom|unsafe|implemented_by|extern|run_tac|run_cmd|run_elab|run_meta|elab|macro|syntax|initialize|builtin_initialize|set_option|Lean|IO|import|namespace|section|end|theorem|def|opaque|instance|class|structure|inductive|attribute|export|open|universe|notation|native_decide|eval|include_str)\b|[`#\"]")


def parse_source(terms):
    quoted = ", ".join(json.dumps(term, ensure_ascii=False) for term in terms)
    return ("import Lean\nimport ClusterSpec\nopen Lean Elab Command\nrun_cmd do\n"
        "  let env ← getEnv\n  let terms : List String := [" + quoted + "]\n"
        "  for term in terms do\n"
        "    if let .error error := Parser.runParserCategory env `term term then\n"
        "      throwError error\n")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def verify(workspace, lean_home):
    started = time.monotonic()
    data = json.loads((workspace / "verification-input.json").read_text())
    policy, logs = data["policy"], []
    report = {"version": 1, "status": "error", "candidate_hash": data["candidate_hash"],
        "spec_hash": data["spec_hash"], "proof_hash": data["proof_hash"], "toolchain": policy["toolchain"],
        "kernel_rechecked": False, "targets": [], "coverage": policy["coverage"]}
    deadline = time.monotonic() + max(2, policy["timeout_seconds"] - 2)
    env = {**os.environ, "LEAN_PATH": str(workspace), "CUDA_VISIBLE_DEVICES": "",
           "PATH": str(lean_home / "bin") + os.pathsep + os.environ.get("PATH", "")}

    def run(*argv):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Lean verification time budget exhausted")
        result = subprocess.run(list(map(str, argv)), cwd=workspace, env=env, capture_output=True,
                                text=True, timeout=remaining)
        output = (result.stdout + result.stderr)[-256000:]
        logs.append("$ " + " ".join(map(str, argv)) + "\n" + output)
        if result.returncode:
            raise ValueError("Lean 检查未通过：" + output[-3000:])
        return output

    try:
        lean, checker = lean_home / "bin" / "lean", lean_home / "bin" / "leanchecker"
        if not lean.is_file() or not checker.is_file():
            raise RuntimeError("验证节点缺少固定版本的 lean / leanchecker，请安装工具链")
        version = run(lean, "--version")
        if not re.search(r"version " + re.escape(policy["toolchain"]) + r"(?:[, )]|$)", version):
            raise RuntimeError("Lean toolchain version differs from frozen verification policy")
        report["toolchain_output"] = version.strip()
        spec = (workspace / "ClusterSpec.lean").read_text()
        raw = (workspace / "proofs.json").read_text()
        if digest(spec) != data["spec_hash"] or digest(raw) != data["proof_hash"]:
            raise RuntimeError("Frozen specification or proof input hash mismatch")
        proofs = json.loads(raw)
        if not isinstance(proofs, dict) or set(proofs) != {target["id"] for target in policy["targets"]}:
            raise ValueError("证明 JSON 必须恰好包含所有登记目标的 ID")
        lines = ["import ClusterSpec", "set_option warningAsError true"]
        for index, target in enumerate(policy["targets"]):
            term = proofs[target["id"]]
            if not isinstance(term, str) or not term.strip() or len(term) > 32000 or FORBIDDEN.search(term):
                raise ValueError(f"目标 {target['id']} 需要有效的受限证明项；不允许 sorry、命令、元编程或 native_decide")
            name = f"ClusterProofs.goal{index}"
            lines.extend([f"theorem {name} : _root_.{target['proposition']} := (\n{term}\n)", f"#print axioms {name}"])
        (workspace / "ClusterProofs.lean").write_text("\n".join(lines) + "\n")
        run(lean, "-DwarningAsError=true", "-o", "ClusterSpec.olean", "ClusterSpec.lean")
        # Parse the entire input as one term before embedding it. A parenthesis
        # or command injection must not escape the fixed theorem declaration.
        (workspace / "ClusterParse.lean").write_text(parse_source(proofs.values()))
        run(lean, "-DwarningAsError=true", "ClusterParse.lean")
        output = run(lean, "-DwarningAsError=true", "-o", "ClusterProofs.olean", "ClusterProofs.lean")
        for index, target in enumerate(policy["targets"]):
            name = f"ClusterProofs.goal{index}"
            match = re.search("'" + re.escape(name) + r"' (?:does not depend on any axioms|depends on axioms: \[([^\]]*)\])", output)
            if not match:
                raise ValueError("缺少可审计的目标公理输出：" + target["id"])
            axioms = [item.strip() for item in (match.group(1) or "").split(",") if item.strip()]
            report["targets"].append({"id": target["id"], "proposition": target["proposition"],
                "status": "passed" if set(axioms) <= ALLOWED_AXIOMS else "unproved", "axioms": axioms})
        if any(target["status"] != "passed" for target in report["targets"]):
            raise ValueError("证明依赖未授权公理；不符合固定公理策略")
        run(checker, "--fresh", "ClusterProofs")
        report["kernel_rechecked"], report["status"] = True, "passed"
    except (ValueError, json.JSONDecodeError) as error:
        report["status"], report["error"] = "unproved", str(error)[-4000:]
    except Exception as error:
        report["error"] = str(error)[-4000:]
    artifacts = workspace / "artifacts"
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    artifacts.mkdir(exist_ok=True)
    (artifacts / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (artifacts / "lean-check.log").write_text("\n".join(logs)[-1000000:])
    for name in ("ClusterSpec.lean", "ClusterParse.lean", "ClusterProofs.lean", "proofs.json", "verification-input.json"):
        path = workspace / name
        if path.is_file():
            (artifacts / name).write_bytes(path.read_bytes())
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-home", type=Path, required=True)
    args = parser.parse_args()
    report = verify(Path.cwd(), args.lean_home.resolve())
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
