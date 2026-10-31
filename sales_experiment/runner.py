from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import signal
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from .config import ExperimentConfig, load_env, results_directory
from .llm import LLMClient, parse_json_object
from .prompts import buyer_opening, buyer_prompt, seller_prompt
from .reporting import write_summary


STOP_REQUESTED = False

TRIAL_PLAN_FIELDS = [
    "trial_index", "repetition", "reference_price", "min_price",
    "discount", "discount_label", "incentive",
    "incentive_label", "high_price_mode", "wtp",
]

TRIAL_CSV_FIELDS = [
    "trial_index", "repetition", "reference_price", "min_price",
    "discount", "discount_label",
    "incentive", "incentive_label", "high_price_mode", "wtp", "outcome",
    "final_price", "total_profit", "seller_earnings", "company_earnings",
    "rounds_used", "input_tokens", "output_tokens", "started_at", "ended_at", "error",
]


def _handle_stop(signum, frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def pct_label(value: float) -> str:
    return f"{value * 100:g}pct"


def sample_wtp(cfg: ExperimentConfig, rng: random.Random) -> float:
    if cfg.wtp_distribution == "uniform":
        return round(rng.uniform(cfg.wtp_min, cfg.wtp_max), 2)
    while True:
        value = rng.gauss(cfg.wtp_mean, cfg.wtp_sd)
        if cfg.wtp_min <= value <= cfg.wtp_max:
            return round(value, 2)


def make_cells(cfg: ExperimentConfig) -> list[dict]:
    cells = []
    incentive_modes: list[tuple[str, float | None, bool]] = [
        (pct_label(x), x, False) for x in cfg.incentives
    ]
    if cfg.include_high_price_no_incentive:
        incentive_modes.append(("high_price_no_incentive", None, True))
    for reference_price in cfg.effective_reference_prices:
        for discount in cfg.discounts:
            min_price = cfg.reservation_price(reference_price, discount)
            for label, incentive, high_price in incentive_modes:
                cells.append({
                    "reference_price": reference_price,
                    "min_price": min_price,
                    "discount": discount,
                    "incentive_label": label,
                    "incentive": incentive,
                    "high_price_mode": high_price,
                })
    return cells


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _maybe_float(value):
    if value is None or value == "":
        return None
    return float(value)


def parse_price(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def normalize_trial_spec(row: dict) -> dict:
    incentive = _maybe_float(row.get("incentive"))
    reference_price = _maybe_float(row.get("reference_price"))
    min_price = _maybe_float(row.get("min_price"))
    discount = float(row["discount"])
    if reference_price is None:
        reference_price = 80.0
    if min_price is None:
        min_price = reference_price * (1 - discount)
    return {
        "trial_index": int(row["trial_index"]),
        "repetition": int(row["repetition"]),
        "reference_price": reference_price,
        "min_price": min_price,
        "discount": discount,
        "discount_label": str(row.get("discount_label") or pct_label(discount)),
        "incentive": incentive,
        "incentive_label": str(row["incentive_label"]),
        "high_price_mode": _parse_bool(row.get("high_price_mode")),
        "wtp": float(row["wtp"]),
    }


def make_trial_plan(cfg: ExperimentConfig, run_dir: Path) -> list[dict]:
    plan_path = run_dir / "trial_plan.csv"
    if plan_path.exists():
        with plan_path.open(newline="", encoding="utf-8") as f:
            return [normalize_trial_spec(row) for row in csv.DictReader(f)]

    rng = random.Random(cfg.random_seed if cfg.random_seed is not None else time.time_ns())
    plan = []
    trial_index = 0
    for cell in make_cells(cfg):
        for repetition in range(1, cfg.repetitions + 1):
            trial_index += 1
            plan.append({
                "trial_index": trial_index,
                "repetition": repetition,
                "reference_price": float(cell["reference_price"]),
                "min_price": float(cell["min_price"]),
                "discount": float(cell["discount"]),
                "discount_label": pct_label(float(cell["discount"])),
                "incentive": cell["incentive"],
                "incentive_label": cell["incentive_label"],
                "high_price_mode": bool(cell["high_price_mode"]),
                "wtp": sample_wtp(cfg, rng),
            })
    write_trial_plan(plan_path, plan)
    return plan


def write_trial_plan(path: Path, plan: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRIAL_PLAN_FIELDS)
        writer.writeheader()
        for row in sorted(plan, key=lambda r: int(r["trial_index"])):
            writer.writerow({
                "trial_index": row["trial_index"],
                "repetition": row["repetition"],
                "reference_price": row["reference_price"],
                "min_price": row["min_price"],
                "discount": row["discount"],
                "discount_label": row["discount_label"],
                "incentive": "" if row["incentive"] is None else row["incentive"],
                "incentive_label": row["incentive_label"],
                "high_price_mode": _bool_text(bool(row["high_price_mode"])),
                "wtp": row["wtp"],
            })


class Agent:
    def __init__(self, system_prompt: str, model: str, temperature: float, client: LLMClient) -> None:
        self.messages = [{"role": "system", "content": system_prompt}]
        self.model = model
        self.temperature = temperature
        self.client = client

    def observe(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def prime_self(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def respond(self) -> dict:
        result = self.client.chat(
            model=self.model,
            messages=self.messages,
            temperature=self.temperature,
        )
        self.messages.append({"role": "assistant", "content": result.content})
        return {
            "raw": result.content,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
        }


def visible_message(parsed: dict) -> str:
    # Never send raw JSON or private reasoning fields to the counterparty.
    message = parsed.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Model output must include a nonempty message string")
    return message


def run_trial(*, cfg: ExperimentConfig, trial: dict) -> dict:
    client = LLMClient(max_tokens=cfg.max_tokens, timeout=cfg.request_timeout)
    try:
        return _run_trial_with_clients(cfg=cfg, trial=trial, seller_client=client, buyer_client=client)
    finally:
        client.close()


def _run_trial_with_clients(
    *,
    cfg: ExperimentConfig,
    trial: dict,
    seller_client: LLMClient,
    buyer_client: LLMClient,
) -> dict:
    wtp = float(trial["wtp"])
    discount = float(trial["discount"])
    reference_price = float(trial["reference_price"])
    min_price = float(trial["min_price"])
    incentive = trial["incentive"]
    high_price_mode = bool(trial["high_price_mode"])

    seller_system = seller_prompt(
        product_cost=cfg.product_cost,
        reference_price=reference_price,
        min_price=min_price,
        incentive=incentive,
        high_price_mode=high_price_mode,
        use_tom_prompt=cfg.use_tom_prompt,
    )
    buyer_system = buyer_prompt(
        wtp=wtp,
        product_cost=cfg.product_cost,
        knowledge_condition=cfg.knowledge_condition,
    )
    seller = Agent(
        seller_system,
        cfg.model_id,
        cfg.temperature,
        seller_client,
    )
    buyer = Agent(
        buyer_system,
        cfg.model_id,
        cfg.temperature,
        buyer_client,
    )

    opening = buyer_opening()
    opening_json = parse_json_object(opening)
    opening_message = visible_message(opening_json)
    # Preserve JSON formatting in the buyer's own conversation history.
    buyer.prime_self(opening)
    seller.observe(opening_message)
    transcript = [{"role": "buyer", "raw": opening, "parsed": opening_json, "round": 0}]

    record = {
        "trial_index": int(trial["trial_index"]),
        "repetition": int(trial["repetition"]),
        "reference_price": reference_price,
        "min_price": min_price,
        "discount": discount,
        "discount_label": trial["discount_label"],
        "incentive": incentive,
        "incentive_label": trial["incentive_label"],
        "high_price_mode": high_price_mode,
        "wtp": wtp,
        "outcome": "no_deal",
        "final_price": None,
        "total_profit": 0.0,
        "seller_earnings": 0.0,
        "company_earnings": 0.0,
        "rounds_used": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ended_at": None,
        "error": None,
        "transcript": transcript,
    }

    try:
        for round_idx in range(1, cfg.max_rounds + 1):
            seller_turn = seller.respond()
            record["input_tokens"] += seller_turn["input_tokens"]
            record["output_tokens"] += seller_turn["output_tokens"]
            try:
                seller_json = parse_json_object(seller_turn["raw"])
            except Exception as exc:
                seller_json = {"parse_error": str(exc)}
            transcript.append({"role": "seller", "raw": seller_turn["raw"], "parsed": seller_json, "round": round_idx})
            buyer.observe(visible_message(seller_json))

            buyer_turn = buyer.respond()
            record["input_tokens"] += buyer_turn["input_tokens"]
            record["output_tokens"] += buyer_turn["output_tokens"]
            try:
                buyer_json = parse_json_object(buyer_turn["raw"])
            except Exception as exc:
                buyer_json = {"parse_error": str(exc)}
            transcript.append({"role": "buyer", "raw": buyer_turn["raw"], "parsed": buyer_json, "round": round_idx})
            seller.observe(visible_message(buyer_json))
            record["rounds_used"] = round_idx

            status = str(buyer_json.get("status", "")).lower()
            if status not in {"continue", "accepted", "rejected"}:
                raise ValueError("Buyer status must be continue, accepted, or rejected")
            if status == "accepted":
                final_price = buyer_json.get("accepted_price")
                if final_price is None:
                    final_price = seller_json.get("offer_price")
                final_price = parse_price(final_price)
                record["outcome"] = "deal"
                record["final_price"] = final_price
                if final_price is not None:
                    profit = final_price - cfg.product_cost
                    incentive_rate = 0.0 if high_price_mode else float(incentive or 0.0)
                    seller_earnings = profit * incentive_rate
                    record["total_profit"] = round(profit, 4)
                    record["seller_earnings"] = round(seller_earnings, 4)
                    record["company_earnings"] = round(profit - seller_earnings, 4)
                break
            if status == "rejected":
                record["outcome"] = "rejected"
                break
            if STOP_REQUESTED:
                record["outcome"] = "stopped"
                break
    except Exception as exc:
        record["outcome"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]

    record["ended_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return record


def write_status(run_dir: Path, data: dict) -> None:
    (run_dir / "status.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_trial_json(run_dir: Path, record: dict) -> None:
    subdir = run_dir / "transcripts"
    subdir.mkdir(exist_ok=True)
    path = subdir / f"trial_{record['trial_index']:05d}.json"
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def flat_record(record: dict) -> dict:
    return {k: v for k, v in record.items() if k != "transcript"}


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRIAL_CSV_FIELDS)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: int(r["trial_index"])):
            writer.writerow({k: row.get(k) for k in TRIAL_CSV_FIELDS})


def read_transcript_row(run_dir: Path, trial_index: int) -> dict | None:
    path = run_dir / "transcripts" / f"trial_{trial_index:05d}.json"
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError, OSError):
        return None
    if (not isinstance(record, dict)
            or not set(TRIAL_CSV_FIELDS).issubset(record)
            or record["trial_index"] != trial_index
            or not isinstance(record.get("transcript"), list)
            or record["outcome"] not in {"deal", "rejected", "no_deal", "error", "stopped"}):
        return None
    return flat_record(record)


def load_completed_rows(run_dir: Path, plan: list[dict]) -> dict[int, dict]:
    # Transcripts are authoritative; this also recovers missing/stale CSV rows.
    completed = {}
    for trial in plan:
        index = int(trial["trial_index"])
        row = read_transcript_row(run_dir, index)
        if row is not None and row["outcome"] not in {"error", "stopped"}:
            completed[index] = row
    return completed


def make_error_record(trial: dict, exc: Exception) -> dict:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "trial_index": int(trial["trial_index"]),
        "repetition": int(trial["repetition"]),
        "reference_price": float(trial["reference_price"]),
        "min_price": float(trial["min_price"]),
        "discount": float(trial["discount"]),
        "discount_label": trial["discount_label"],
        "incentive": trial["incentive"],
        "incentive_label": trial["incentive_label"],
        "high_price_mode": bool(trial["high_price_mode"]),
        "wtp": float(trial["wtp"]),
        "outcome": "error",
        "final_price": None,
        "total_profit": 0.0,
        "seller_earnings": 0.0,
        "company_earnings": 0.0,
        "rounds_used": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "started_at": now,
        "ended_at": now,
        "error": f"{type(exc).__name__}: {exc}"[:500],
        "transcript": [],
    }


def trial_status_payload(trial: dict) -> dict:
    return {
        "trial_index": int(trial["trial_index"]),
        "reference_price": trial.get("reference_price"),
        "discount": trial["discount_label"],
        "incentive": trial["incentive_label"],
        "repetition": int(trial["repetition"]),
    }


def base_status(
    *,
    state: str,
    started_at: str,
    total_trials: int,
    completed_trials: int,
    parallel_workers: int,
    pending_count: int,
    active_trials: list[dict],
    resume_count: int,
    last_resumed_at: str | None,
    ended_at: str | None = None,
) -> dict:
    return {
        "state": state,
        "pid": os.getpid(),
        "started_at": started_at,
        "ended_at": ended_at,
        "completed_trials": completed_trials,
        "total_trials": total_trials,
        "remaining_trials": max(0, total_trials - completed_trials),
        "pending_trials": pending_count,
        "running_trials": len(active_trials),
        "active_trials": active_trials,
        "parallel_workers": parallel_workers,
        "resume_count": resume_count,
        "last_resumed_at": last_resumed_at,
        "current_cell": active_trials[0] if active_trials else None,
    }


def run_experiment(run_dir: Path, cfg: ExperimentConfig, *, resume: bool = False, trial_runner=run_trial) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = False
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    cfg.validate()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcripts").mkdir(exist_ok=True)
    config_path = run_dir / "config.json"
    if not resume or not config_path.exists():
        cfg.write_json(config_path)

    plan = make_trial_plan(cfg, run_dir)
    total_trials = len(plan)
    completed_by_index = load_completed_rows(run_dir, plan)
    rows: list[dict] = list(completed_by_index.values())
    pending = [trial for trial in plan if int(trial["trial_index"]) not in completed_by_index]
    old_status = {}
    status_path = run_dir / "status.json"
    if status_path.exists():
        try:
            old_status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old_status = {}
    started_at = old_status.get("started_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    last_resumed_at = datetime.now().astimezone().isoformat(timespec="seconds") if resume else old_status.get("last_resumed_at")
    resume_count = int(old_status.get("resume_count", 0) or 0) + (1 if resume else 0)
    jsonl_path = run_dir / "trials.jsonl"
    parallel_workers = min(max(int(cfg.parallel_workers), 1), 64)
    stop_file = run_dir / ".stop-requested"

    def check_stop_request() -> None:
        global STOP_REQUESTED
        if stop_file.is_file():
            STOP_REQUESTED = True

    write_csv(run_dir / "trials.csv", rows)
    write_summary(run_dir, rows, cfg)

    if not pending:
        write_status(run_dir, base_status(
            state="completed",
            started_at=started_at,
            ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            total_trials=total_trials,
            completed_trials=len(rows),
            parallel_workers=parallel_workers,
            pending_count=0,
            active_trials=[],
            resume_count=resume_count,
            last_resumed_at=last_resumed_at,
        ))
        return

    pending_queue = list(pending)
    active: dict[Future, dict] = {}

    def write_running_status() -> None:
        write_status(run_dir, base_status(
            state="running",
            started_at=started_at,
            total_trials=total_trials,
            completed_trials=len(rows),
            parallel_workers=parallel_workers,
            pending_count=len(pending_queue),
            active_trials=[trial_status_payload(t) for t in active.values()],
            resume_count=resume_count,
            last_resumed_at=last_resumed_at,
        ))

    def submit_until_full(executor: ThreadPoolExecutor) -> None:
        check_stop_request()
        while not STOP_REQUESTED and pending_queue and len(active) < parallel_workers:
            trial = pending_queue.pop(0)
            print(
                f"[submit {trial['trial_index']}/{total_trials}] "
                f"ref={trial.get('reference_price')} discount={trial['discount_label']} "
                f"incentive={trial['incentive_label']} rep={trial['repetition']}",
                flush=True,
            )
            future = executor.submit(trial_runner, cfg=cfg, trial=trial)
            active[future] = trial

    write_running_status()
    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        submit_until_full(executor)
        write_running_status()
        while active:
            check_stop_request()
            done, _ = wait(active.keys(), timeout=1.0, return_when=FIRST_COMPLETED)
            if not done:
                if STOP_REQUESTED:
                    write_running_status()
                continue
            for future in done:
                trial = active.pop(future)
                try:
                    record = future.result()
                except Exception as exc:
                    record = make_error_record(trial, exc)
                trial_index = int(record["trial_index"])
                completed_by_index[trial_index] = flat_record(record)
                rows = list(completed_by_index.values())
                append_jsonl(jsonl_path, record)
                write_trial_json(run_dir, record)
                write_csv(run_dir / "trials.csv", rows)
                write_summary(run_dir, rows, cfg)
                print(
                    f"  -> trial {trial_index}/{total_trials} {record['outcome']} "
                    f"price={record['final_price']} rounds={record['rounds_used']}",
                    flush=True,
                )
            submit_until_full(executor)
            write_running_status()

    final_state = "stopped" if STOP_REQUESTED or pending_queue else "completed"
    write_status(run_dir, base_status(
        state=final_state,
        started_at=started_at,
        ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        total_trials=total_trials,
        completed_trials=len(rows),
        parallel_workers=parallel_workers,
        pending_count=len(pending_queue),
        active_trials=[],
        resume_count=resume_count,
        last_resumed_at=last_resumed_at,
    ))
    write_csv(run_dir / "trials.csv", rows)
    write_summary(run_dir, rows, cfg)
    stop_file.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bilateral LLM bargaining experiments.")
    parser.add_argument("--config", type=Path, help="JSON experiment configuration (required for a new run)")
    parser.add_argument("--env-file", type=Path, help="Credentials file; defaults to .env in the working directory")
    parser.add_argument("--results-dir", type=Path, help="Parent for generated run folders; overrides BARGAINING_RESULTS_DIR")
    parser.add_argument("--run-dir", type=Path, help="Explicit run folder; required for --resume")
    parser.add_argument("--resume", action="store_true", help="Resume the saved config and trial plan; retry errors and stopped trials")
    args = parser.parse_args(argv)

    try:
        env_base = load_env(args.env_file)
        if args.resume:
            if args.run_dir is None:
                raise ValueError("--resume requires --run-dir")
            if args.config is not None:
                raise ValueError("--resume uses the saved config.json; omit --config")
            run_dir = args.run_dir.expanduser().resolve()
            if not (run_dir / "trial_plan.csv").is_file():
                raise ValueError("Cannot resume without the original trial_plan.csv")
            cfg = ExperimentConfig.from_json_file(run_dir / "config.json")
        else:
            if args.config is None:
                raise ValueError("A new run requires --config")
            cfg = ExperimentConfig.from_json_file(args.config.expanduser())
            parent = args.results_dir.expanduser().resolve() if args.results_dir else results_directory(env_base)
            run_dir = (args.run_dir.expanduser().resolve() if args.run_dir else
                       parent / datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            if run_dir.exists() and (not run_dir.is_dir() or any(run_dir.iterdir())):
                raise ValueError("Run folder is not empty; choose a new folder or use --resume")
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise ValueError("Set OPENAI_API_KEY in .env or the environment before running live trials")
        print(f"Run folder: {run_dir}", flush=True)
        (run_dir / ".stop-requested").unlink(missing_ok=True)
        run_experiment(run_dir, cfg, resume=args.resume)
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        if status["state"] != "completed":
            return 130
        rows = load_completed_rows(run_dir, make_trial_plan(cfg, run_dir))
        return 0 if len(rows) == cfg.total_trials else 1
    except (ValueError, OSError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
