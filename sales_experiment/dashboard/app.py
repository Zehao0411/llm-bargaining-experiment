from __future__ import annotations

import hmac
import math
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, send_file, session, url_for

from ..config import ExperimentConfig, load_env, results_directory
from . import reader
from .manager import RunManager, read_json


def config_from_form(form) -> ExperimentConfig:
    values = {}
    integers = {"repetitions", "max_rounds", "max_tokens", "parallel_workers"}
    optional = {"random_seed", "fixed_min_price", "audit_price_ceiling"}
    lists = {"discounts", "incentives", "reference_prices"}
    strings = {"model_id", "knowledge_condition", "wtp_distribution"}
    booleans = {"use_tom_prompt", "include_high_price_no_incentive"}
    for name in ExperimentConfig.__dataclass_fields__:
        raw = str(form.get(name, "")).strip()
        try:
            if name in booleans:
                values[name] = form.get(name) == "on"
            elif name in strings:
                values[name] = raw
            elif name in lists:
                values[name] = [float(value.strip()) for value in raw.split(",") if value.strip()]
            elif name in optional:
                values[name] = None if not raw else (int(raw) if name == "random_seed" else float(raw))
            else:
                values[name] = int(raw) if name in integers else float(raw)
        except ValueError as exc:
            raise ValueError(f"Enter a valid value for {name.replace('_', ' ')}") from exc
    return ExperimentConfig.from_dict(values)


def create_app(*, results_dir: Path | None = None, env_file: Path | None = None,
               initial_config: Path | None = None) -> Flask:
    env_base = load_env(env_file)
    manager = RunManager(results_dir or results_directory(env_base))
    defaults = ExperimentConfig.from_json_file(initial_config) if initial_config else ExperimentConfig()
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=65536)
    app.extensions["run_manager"] = manager

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def local_requests():
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            abort(403, description="The dashboard is available on this computer only")
        if urlsplit(request.host_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            abort(403, description="Use the local dashboard address")
        if request.method == "POST" and not hmac.compare_digest(
            str(session.get("csrf", "")), str(request.form.get("csrf_token", ""))
        ):
            abort(400, description="Reload the page before submitting this action")
        if request.method == "POST" and not session.get("csrf"):
            abort(400, description="Reload the page before submitting this action")

    @app.template_filter("money")
    def money(value):
        try:
            return f"${float(value):,.2f}" if value is not None and math.isfinite(float(value)) else "—"
        except (ValueError, TypeError):
            return "—"

    @app.template_filter("percent")
    def percent(value):
        return f"{float(value):.1%}" if value is not None else "—"

    @app.template_filter("rate_label")
    def rate_label(value):
        return "No commission" if value == "high_price_no_incentive" else str(value).replace("pct", "%")

    def get_run(run_id):
        try:
            if not (manager.run_dir(run_id) / "config.json").is_file():
                abort(404, description="Experiment not found")
            return reader.run_data(manager, run_id)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            abort(400, description=str(exc))

    def index_page(values=None, error=None):
        return render_template("index.html", values=values if values is not None else defaults.to_dict(),
                               runs=reader.list_runs(manager), error=error)

    @app.get("/")
    def index():
        return index_page()

    @app.get("/runs")
    def runs():
        return render_template("_runs.html", runs=reader.list_runs(manager))

    @app.post("/start")
    def start():
        try:
            run_id = manager.start(config_from_form(request.form))
        except (ValueError, OSError, TypeError) as exc:
            values = defaults.to_dict() | request.form.to_dict()
            for name in ("use_tom_prompt", "include_high_price_no_incentive"):
                values[name] = request.form.get(name) == "on"
            return index_page(values, str(exc)), 400
        return redirect(url_for("run_detail", run_id=run_id))

    @app.get("/run/<run_id>")
    def run_detail(run_id):
        data = get_run(run_id)
        outcome = request.args.get("outcome", "")
        selected = [row for row in data["rows"] if not outcome or row["outcome"] == outcome]
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        pages = max(1, math.ceil(len(selected) / 100))
        page = min(page, pages)
        return render_template("run_detail.html", run=data, trials=selected[(page - 1) * 100:page * 100],
                               outcome=outcome, page=page, pages=pages, matched=len(selected),
                               log=reader.log_tail(manager.run_dir(run_id)))

    @app.post("/run/<run_id>/stop")
    def stop(run_id):
        get_run(run_id)
        try:
            manager.stop(run_id)
        except (ValueError, OSError) as exc:
            abort(400, description=str(exc))
        return redirect(url_for("run_detail", run_id=run_id))

    @app.post("/run/<run_id>/resume")
    def resume(run_id):
        data = get_run(run_id)
        if not data["can_resume"]:
            abort(400, description="There are no incomplete or failed trials to resume")
        try:
            manager.resume(run_id)
        except (ValueError, OSError) as exc:
            abort(400, description=str(exc))
        return redirect(url_for("run_detail", run_id=run_id))

    @app.get("/run/<run_id>/trial/<int:trial_index>")
    def trial_detail(run_id, trial_index):
        data = get_run(run_id)
        trial = read_json(manager.run_dir(run_id) / "transcripts" / f"trial_{trial_index:05d}.json", None)
        if not isinstance(trial, dict):
            abort(404, description="Transcript not found")
        row = next((row for row in data["rows"] if row["trial_index"] == trial_index), None)
        return render_template("trial_detail.html", run=data, trial=trial, row=row)

    @app.get("/run/<run_id>/download/<filename>")
    def download(run_id, filename):
        get_run(run_id)
        allowed = {"config.json", "trial_plan.csv", "trials.csv", "trials.jsonl", "summary_by_cell.csv",
                   "summary.json", "price_audit.csv", "experiment.log"}
        if filename not in allowed:
            abort(404)
        path = manager.run_dir(run_id) / filename
        if not path.is_file():
            abort(404, description="This file is not available yet")
        return send_file(path, as_attachment=True, download_name=f"{run_id}_{filename}")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def error_page(error):
        return render_template("error.html", message=error.description, code=error.code), error.code

    return app
