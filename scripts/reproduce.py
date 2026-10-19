"""One-command offline software evidence. Synthetic results never qualify a market model."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new empty artifact directory")
    args = parser.parse_args()
    root = Path(args.output).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("use a fresh output directory; previous evidence is never overwritten")
    root.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from core.provenance import git_revision, source_hash
    env = {**os.environ, "TRADING_MODE": "paper", "ENABLE_LIVE_TRADING": "false",
           "I_UNDERSTAND_REAL_MONEY_RISK": "false", "USE_ML_FILTER": "false"}
    data = root / "diagnostic" / "fixture"
    common = ["--data-dir", str(data), "--symbol", "FIXTURE", "--start", "2024-01-15T00:00:00Z",
              "--end", "2024-01-15T06:00:00Z", "--calendar", "24/7"]
    commands = [
        ["diagnostic", "--output", str(root / "diagnostic")],
        ["compete", *common, "--budget", "48", "--output", str(root / "competition")],
        ["ml-compare", *common, "--output", str(root / "ml-comparison")],
        ["model-diagnostic", "--output", str(root / "model-diagnostic")],
        ["ai-research", *common, "--budget", "48", "--output", str(root / "research")],
        ["forward-diagnostic", "--output", str(root / "forward-diagnostic")],
    ]
    outcomes = []
    for index, command in enumerate(commands):
        started = time.monotonic()
        invocation = [sys.executable, "main.py", *command]
        log = root / f"{index:02d}-{command[0]}.log"
        print(f"Running {command[0]} (SYNTHETIC SOFTWARE EVIDENCE)", flush=True)
        with log.open("w", encoding="utf-8") as handle:
            result = subprocess.run(invocation, cwd=repo, env=env, stdout=handle, stderr=subprocess.STDOUT, check=False)
        outcomes.append({"command": invocation, "exit_code": result.returncode,
                         "seconds": round(time.monotonic() - started, 3), "log": log.name})
        if result.returncode:
            break
    report = {"dataset_kind": "synthetic", "market_data_accessed": False, "llm_inference_run": False,
        "notice": "SOFTWARE VALIDATION ONLY. NO TRADING OR PROFITABILITY EVIDENCE.",
        "git_sha": git_revision(), "source_sha256": source_hash(), "python": platform.python_version(),
        "packages": {name: importlib.metadata.version(name) for name in ("pandas", "numpy", "scikit-learn")},
        "commands": outcomes, "status": "passed" if len(outcomes) == len(commands) and not outcomes[-1]["exit_code"] else "failed"}
    (root / "reproduction.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    hashes = {str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(root.rglob("*")) if path.is_file() and path.name != "writer.lock"}
    (root / "manifest.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
