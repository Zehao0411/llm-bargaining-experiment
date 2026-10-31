"""Summarize recorded outcomes and audit agreement prices after negotiation."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from .config import ExperimentConfig


def audit_record(row: dict, cfg: ExperimentConfig) -> dict:
    deal = row["outcome"] == "deal"
    raw_price = row.get("final_price")
    price = float(raw_price) if raw_price not in (None, "") else None
    missing = deal and (price is None or not math.isfinite(price))
    annual = bool(deal and not missing and cfg.audit_price_ceiling is not None
                  and price > cfg.audit_price_ceiling)
    below = bool(deal and not missing and price < float(row["min_price"]))
    above = bool(deal and not missing and price > float(row["wtp"]))
    valid = deal and not (missing or annual or below or above)
    supplementary_error = row["outcome"] == "error" and row.get("high_price_mode") is True
    retained = valid or row["outcome"] in {"rejected", "no_deal"} or supplementary_error
    return {
        "trial_index": row["trial_index"],
        "recorded_deal": deal,
        "missing_or_nonfinite_price": missing,
        "above_audit_ceiling": annual,
        "below_seller_floor": below,
        "above_buyer_wtp": above,
        "valid_agreement": valid,
        "retained_monetary_attempt": retained,
        "screened_total_profit": price - cfg.product_cost if valid else (0.0 if retained else None),
        "screened_buyer_surplus": float(row["wtp"]) - price if valid else (0.0 if retained else None),
    }


def write_summary(run_dir: Path, rows: list[dict], cfg: ExperimentConfig) -> None:
    groups: dict[tuple, list[tuple[dict, dict]]] = {}
    audits = []
    for row in sorted(rows, key=lambda r: int(r["trial_index"])):
        audit = audit_record(row, cfg)
        audits.append(audit)
        key = (float(row["reference_price"]), row["discount_label"], row["incentive_label"])
        groups.setdefault(key, []).append((row, audit))
    summaries = []
    for (reference_price, discount, incentive), pairs in sorted(groups.items()):
        valid = [(r, a) for r, a in pairs if a["valid_agreement"]]
        retained = [(r, a) for r, a in pairs if a["retained_monetary_attempt"]]
        n = len(pairs)
        gross = sum(a["screened_total_profit"] for _, a in retained)
        summaries.append({
            "reference_price": reference_price,
            "discount_label": discount,
            "incentive_label": incentive,
            "n_recorded_attempts": n,
            "n_errors": sum(r["outcome"] == "error" for r, _ in pairs),
            "n_stopped": sum(r["outcome"] == "stopped" for r, _ in pairs),
            "n_recorded_agreements": sum(a["recorded_deal"] for _, a in pairs),
            "recorded_agreement_rate": sum(a["recorded_deal"] for _, a in pairs) / n,
            "n_above_audit_ceiling": sum(a["above_audit_ceiling"] for _, a in pairs),
            "n_other_invalid_agreements": sum(a["recorded_deal"] and not a["valid_agreement"]
                                               and not a["above_audit_ceiling"] for _, a in pairs),
            "n_valid_agreements": len(valid),
            "n_retained_monetary_attempts": len(retained),
            "mean_valid_price": sum(float(r["final_price"]) for r, _ in valid) / len(valid) if valid else None,
            "mean_screened_gross_earnings": gross / len(retained) if retained else None,
            "mean_screened_seller_commission": sum(float(r["seller_earnings"]) for r, _ in retained) / len(retained) if retained else None,
            "mean_screened_company_earnings": sum(float(r["company_earnings"]) for r, _ in retained) / len(retained) if retained else None,
            "mean_screened_buyer_surplus": sum(a["screened_buyer_surplus"] for _, a in retained) / len(retained) if retained else None,
            "mean_all_attempt_gross_earnings": gross / n,
            "mean_rounds": sum(int(r["rounds_used"]) for r, _ in pairs) / n,
        })
    for name, items in (("summary_by_cell.csv", summaries), ("price_audit.csv", audits)):
        with (run_dir / name).open("w", newline="", encoding="utf-8") as stream:
            if items:
                writer = csv.DictWriter(stream, fieldnames=list(items[0]))
                writer.writeheader()
                writer.writerows(items)
    (run_dir / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
