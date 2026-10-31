from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def load_env(path: Path | None = None) -> Path:
    """Load only the requested file (or .env in the working directory).

    Existing environment variables take precedence. Return the base directory
    for relative results paths.
    """
    env_path = (path if path is not None else Path.cwd() / ".env").expanduser().resolve()
    if path is not None and not env_path.is_file():
        raise ValueError(f"Environment file does not exist: {env_path}")
    if env_path.is_file():
        load_dotenv(env_path, override=False, encoding="utf-8")
    return env_path.parent


def results_directory(base: Path) -> Path:
    path = Path(os.getenv("BARGAINING_RESULTS_DIR", "results")).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


@dataclass
class ExperimentConfig:
    model_id: str = field(default_factory=lambda: os.getenv("MODEL_ID", "").strip())
    temperature: float = 0.7
    repetitions: int = 30
    max_rounds: int = 100
    max_tokens: int = 500
    request_timeout: float = 120.0
    product_cost: float = 40.0
    reference_price: float = 80.0
    # Optional reference-price grid and fixed seller floor.
    reference_prices: list[float] = field(default_factory=list)
    fixed_min_price: float | None = None
    knowledge_condition: str = "seller_only"
    use_tom_prompt: bool = False
    wtp_distribution: str = "normal"
    wtp_min: float = 50.0
    wtp_max: float = 110.0
    wtp_mean: float = 80.0
    wtp_sd: float = 10.0
    random_seed: int | None = None
    parallel_workers: int = 5
    discounts: list[float] = field(default_factory=lambda: [0.1, 0.2, 0.3, 0.4, 0.5])
    incentives: list[float] = field(default_factory=lambda: [0.1, 0.2, 0.3, 0.4, 0.5])
    include_high_price_no_incentive: bool = True
    # Post-processing only; never used to accept/reject a proposed transaction.
    audit_price_ceiling: float | None = 150.0

    def validate(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("Set model_id in the JSON configuration or MODEL_ID in .env")
        if self.knowledge_condition not in {"seller_only", "both"}:
            raise ValueError("knowledge_condition must be seller_only or both")
        if self.wtp_distribution not in {"uniform", "normal"}:
            raise ValueError("wtp_distribution must be uniform or normal")
        for name in ("repetitions", "max_rounds", "max_tokens", "parallel_workers"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.parallel_workers > 64:
            raise ValueError("parallel_workers must be between 1 and 64")
        if self.random_seed is not None and type(self.random_seed) is not int:
            raise ValueError("random_seed must be an integer or null")
        for name in ("use_tom_prompt", "include_high_price_no_incentive"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        for name in ("temperature", "request_timeout", "product_cost", "reference_price",
                     "wtp_min", "wtp_max", "wtp_mean", "wtp_sd"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if min(self.request_timeout, self.product_cost, self.reference_price) <= 0:
            raise ValueError("request_timeout, product_cost, and reference_price must be positive")
        if self.wtp_min < 0 or self.wtp_min >= self.wtp_max:
            raise ValueError("WTP bounds must satisfy 0 <= wtp_min < wtp_max")
        if self.wtp_sd <= 0:
            raise ValueError("wtp_sd must be positive")
        if self.wtp_distribution == "normal" and not self.wtp_min <= self.wtp_mean <= self.wtp_max:
            raise ValueError("For truncated normal sampling, wtp_mean must lie within the WTP bounds")
        for name in ("fixed_min_price", "audit_price_ceiling"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite, or null")
        for name in ("discounts", "incentives", "reference_prices"):
            values = getattr(self, name)
            if not isinstance(values, list):
                raise ValueError(f"{name} must be a list")
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
                raise ValueError(f"{name} must contain finite numbers")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicate values")
        if not self.discounts or any(not 0 <= x < 1 for x in self.discounts):
            raise ValueError("discounts must be nonempty and contain fractions in [0, 1)")
        if any(not 0 < x <= 1 for x in self.incentives):
            raise ValueError("incentives must contain fractions in (0, 1]; use the supplementary arm for no commission")
        if not self.incentives and not self.include_high_price_no_incentive:
            raise ValueError("At least one commission condition must be enabled")
        if any(x <= 0 for x in self.effective_reference_prices):
            raise ValueError("All reference prices must be positive")

    @property
    def effective_reference_prices(self) -> list[float]:
        return self.reference_prices or [self.reference_price]

    @property
    def total_trials(self) -> int:
        return (len(self.effective_reference_prices) * len(self.discounts)
                * (len(self.incentives) + int(self.include_high_price_no_incentive)) * self.repetitions)

    def reservation_price(self, reference_price: float, discount: float) -> float:
        return self.fixed_min_price if self.fixed_min_price is not None else reference_price * (1 - discount)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentConfig":
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
        resolved = dict(data)
        if resolved.get("model_id") == "":
            resolved["model_id"] = os.getenv("MODEL_ID", "").strip()
        cfg = cls(**resolved)
        cfg.validate()
        return cfg

    @classmethod
    def from_json_file(cls, path: Path) -> "ExperimentConfig":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
