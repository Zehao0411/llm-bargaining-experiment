from __future__ import annotations

from pathlib import Path


_TEMPLATE_DIR = Path(__file__).resolve().parent / "prompt_templates"


def _read(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8").strip()


def seller_prompt(*, product_cost: float, reference_price: float, min_price: float,
                  incentive: float | None, high_price_mode: bool,
                  use_tom_prompt: bool = False) -> str:
    if high_price_mode:
        incentive_text = (
            "You receive no explicit commission. Your goal is to sell at the highest possible price while still closing a deal."
        )
    else:
        incentive_text = (
            f"If a deal closes, your compensation is based on {incentive * 100:g}% of seller profit, "
            "defined as final_price - product_cost."
        )
    return _read("seller_body.txt").format(
        product_cost=f"{product_cost:.2f}", reference_price=f"{reference_price:.2f}",
        min_price=f"{min_price:.2f}", incentive_text=incentive_text,
        tom_text=_read("seller_tom.txt") if use_tom_prompt else "",
        output_schema=_read("seller_schema_tom.txt" if use_tom_prompt else "seller_schema_standard.txt"),
    )


def buyer_prompt(*, wtp: float, product_cost: float, knowledge_condition: str) -> str:
    cost_text = (f"You know the product cost is ${product_cost:.2f}."
                 if knowledge_condition == "both" else "You do not know the product cost.")
    return _read("buyer_body.txt").format(
        wtp=f"{wtp:.2f}", cost_text=cost_text, output_schema=_read("buyer_output_schema.txt"),
    )


def buyer_opening() -> str:
    return _read("buyer_opening.json")
