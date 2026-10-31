from __future__ import annotations

import csv
import math
from pathlib import Path

from ..config import ExperimentConfig
from ..reporting import audit_record
from .manager import RunManager, read_json


def read_trials(rd: Path) -> list[dict]:
    try:
        with (rd / "trials.csv").open(newline="", encoding="utf-8") as stream:
            source = list(csv.DictReader(stream))
    except (OSError, csv.Error):
        return []
    rows = []
    for row in source:
        try:
            row["trial_index"] = int(row["trial_index"])
            row["rounds_used"] = int(row["rounds_used"])
            for name in ("reference_price", "min_price", "discount", "incentive", "wtp", "final_price",
                         "total_profit", "seller_earnings", "company_earnings"):
                value = float(row[name]) if row.get(name) not in (None, "") else None
                row[name] = value if value is not None and math.isfinite(value) else None
            row["high_price_mode"] = str(row.get("high_price_mode", "")).lower() == "true"
            if row.get("outcome") not in {"deal", "rejected", "no_deal", "error", "stopped"}:
                continue
            if any(row.get(name) is None for name in ("reference_price", "min_price", "discount", "wtp",
                                                      "total_profit", "seller_earnings", "company_earnings")):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: row["trial_index"])


def run_data(manager: RunManager, run_id: str) -> dict:
    rd = manager.run_dir(run_id)
    config = read_json(rd / "config.json", {})
    status = read_json(rd / "status.json", {})
    running = manager.is_running(run_id)
    if not running and status.get("state") in {"running", "starting"}:
        status = {**status, "state": "interrupted", "active_trials": [], "running_trials": 0}
    cfg = ExperimentConfig.from_dict(config)
    rows = read_trials(rd)
    for row in rows:
        row["audit"] = audit_record(row, cfg)
    valid = [r for r in rows if r["audit"]["valid_agreement"]]
    retained = [r for r in rows if r["audit"]["retained_monetary_attempt"]]
    n = len(rows)
    kpis = {
        "recorded": n,
        "agreements": sum(r["outcome"] == "deal" for r in rows),
        "valid_agreements": len(valid),
        "invalid_agreements": sum(r["outcome"] == "deal" and not r["audit"]["valid_agreement"] for r in rows),
        "errors": sum(r["outcome"] == "error" for r in rows),
        "retained": len(retained),
        "deal_rate": sum(r["outcome"] == "deal" for r in rows) / n if n else None,
        "price": sum(r["final_price"] for r in valid) / len(valid) if valid else None,
        "rounds": sum(r["rounds_used"] for r in rows) / n if n else None,
        "gross": sum(r["audit"]["screened_total_profit"] for r in retained) / len(retained) if retained else None,
        "company": sum(r["company_earnings"] or 0 for r in retained) / len(retained) if retained else None,
        "commission": sum(r["seller_earnings"] or 0 for r in retained) / len(retained) if retained else None,
    }
    retryable = any(row["outcome"] in {"error", "stopped"} for row in rows)
    missing = len(rows) < cfg.total_trials or any(
        not (rd / "transcripts" / f"trial_{row['trial_index']:05d}.json").is_file() for row in rows
    )
    total = int(status.get("total_trials") or cfg.total_trials)
    return {
        "run_id": run_id, "cfg": config, "status": status, "running": running,
        "total": total, "progress": min(100, 100 * n / total) if total else 0,
        "rows": rows, "kpis": kpis, "summary": read_json(rd / "summary.json", []),
        "can_resume": not running and (retryable or missing) and (rd / "trial_plan.csv").is_file(),
    }


def list_runs(manager: RunManager) -> list[dict]:
    if not manager.root.exists():
        return []
    runs = []
    for rd in sorted(manager.root.iterdir(), reverse=True):
        if not rd.is_dir() or not (rd / "config.json").is_file():
            continue
        try:
            runs.append(run_data(manager, rd.name))
        except (ValueError, OSError, TypeError, KeyError):
            continue
    return runs


def log_tail(rd: Path, lines: int = 100) -> str:
    try:
        with (rd / "experiment.log").open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 32000))
            return "\n".join(stream.read().decode("utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""
