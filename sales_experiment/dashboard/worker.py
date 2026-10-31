from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from ..config import ExperimentConfig
from ..runner import run_experiment
from .manager import read_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    rd = args.run_dir.resolve()
    try:
        cfg = ExperimentConfig.from_json_file(rd / "config.json")
        run_experiment(rd, cfg, resume=args.resume)
        return 0
    except Exception as exc:
        status = read_json(rd / "status.json", {})
        status.update(state="error", pid=os.getpid(), error=f"{type(exc).__name__}: {exc}",
                      ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                      running_trials=0, active_trials=[])
        (rd / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        print(status["error"], flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
