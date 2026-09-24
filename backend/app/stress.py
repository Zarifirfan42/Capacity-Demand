"""Seeded synthetic months for the modelled gap.

The October book is built short. Each month also draws utilisation from 70% to 130%
of dated capacity and redraws contract types, so some months have no shortage.
The gap is at least zero because the recommendation minimises the objective it is scored on.
Nothing here is annualised from a single month without saying so.
"""

from __future__ import annotations

import json
import random
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from app.db import database_path
from app.economics import HORIZON_END, HORIZON_START, round_m3, round_rm
from app.engine import allocate_world, load_world
from app.forecast import SEASONAL

STRESS_SEED = 202610
STRESS_MONTHS = 200
REPORT_NAME = "stress_report.json"


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    weight = index - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _shift_date(iso: str, days: int) -> str:
    start = date.fromisoformat(HORIZON_START)
    end = date.fromisoformat(HORIZON_END)
    shifted = date.fromisoformat(iso) + timedelta(days=days)
    if shifted < start:
        shifted = start
    if shifted > end:
        shifted = end
    return shifted.isoformat()


def synthetic_month(base: dict, rng: random.Random) -> dict:
    world = deepcopy(base)
    # 130% of this calendar cannot clear the 9 Oct crunch (635 m³ of supply against about 1,410 m³ due).
    # The draw therefore runs from 70% to 250%, and some months also spread required dates across the horizon.
    utilisation = rng.uniform(0.70, 2.50)
    for row in world["calendar"]:
        row["available_capacity"] = max(0.0, float(row["available_capacity"]) * utilisation)
    seasonal = SEASONAL[rng.randint(1, 12)]
    spread_dates = rng.random() < 0.40
    start = date.fromisoformat(HORIZON_START)
    for demand in world["demands"]:
        scale = seasonal * rng.uniform(0.85, 1.15)
        demand["requested_quantity"] = max(1.0, float(demand["requested_quantity"]) * scale)
        demand["confirmed_quantity"] = min(float(demand["confirmed_quantity"]) * scale, float(demand["requested_quantity"]))
        if spread_dates:
            demand["required_date"] = (start + timedelta(days=rng.randint(0, 29))).isoformat()
        else:
            demand["required_date"] = _shift_date(str(demand["required_date"]), rng.randint(-3, 3))
        if float(demand.get("contractual_penalty") or 0) > 0:
            demand["penalty_type"] = rng.choice(("lump_sum", "per_m3"))
        delay_pool = float(demand.get("delay_days_if_unserved") or 0) * float(demand.get("delay_cost_per_day") or 0)
        if demand.get("demand_type") == "Internal" and delay_pool > 0:
            demand["delay_type"] = rng.choice(("lump_days", "proportional", "per_day"))
    world["utilisation"] = round(utilisation, 4)
    return world


def run_stress(months: int = STRESS_MONTHS, seed: int = STRESS_SEED) -> dict:
    rng = random.Random(seed)
    base = load_world()
    gaps: list[float] = []
    constrained_gaps: list[float] = []
    for _ in range(months):
        world = synthetic_month(base, rng)
        result = allocate_world(world, include_comparison=False, include_expedite=False, lex=False)
        gap = float(result["totals"]["value_protected_vs_best_rule_rm"])
        gaps.append(gap)
        if float(result["totals"]["dated_shortfall_m3"]) > 0.5:
            constrained_gaps.append(gap)
    unconditional_mean = sum(gaps) / len(gaps) if gaps else 0.0
    conditional_mean = sum(constrained_gaps) / len(constrained_gaps) if constrained_gaps else 0.0
    share = len(constrained_gaps) / months if months else 0.0
    p10 = _percentile(gaps, 0.10)
    p50 = _percentile(gaps, 0.50)
    p90 = _percentile(gaps, 0.90)
    report = {
        "months": months,
        "seed": seed,
        "constrained_months": len(constrained_gaps),
        "constrained_share": round(share, 4),
        "unconditional": {
            "mean_rm": round_rm(unconditional_mean),
            "p10_rm": round_rm(p10),
            "p50_rm": round_rm(p50),
            "p90_rm": round_rm(p90),
            "share_gap_positive": round(sum(1 for gap in gaps if gap > 0.5) / months, 4) if months else 0.0,
        },
        "given_constrained": {
            "mean_rm": round_rm(conditional_mean),
            "p10_rm": round_rm(_percentile(constrained_gaps, 0.10)),
            "p50_rm": round_rm(_percentile(constrained_gaps, 0.50)),
            "p90_rm": round_rm(_percentile(constrained_gaps, 0.90)),
        },
        "annualised_synthetic": {
            "mean_rm": round_rm(12 * unconditional_mean),
            "p10_rm": round_rm(12 * p10),
            "p90_rm": round_rm(12 * p90),
            "label": "12 × the unconditional monthly gap. Synthetic. It assumes this draw's mix of quiet and short months.",
        },
        "synthetic_constrained_months_per_year": round_m3(12 * share),
        "caveat": (
            "The recommendation minimises the same objective the gap is scored on, so the gap is at least zero by construction. "
            "The magnitude is meaningful only if the seeded inputs are. "
            "Stress solves the objective without the lexicographic tie-break. That tie-break moves a plan by at most the RM10 epsilon. "
            "Utilisation is drawn from 70% to 250% of dated capacity, because 130% cannot clear this book's 9 Oct crunch. "
            "Four in ten months also spread required dates across the horizon. Quantities move with a seasonal index, and contract types are redrawn because the seeded types are unverified."
        ),
    }
    path = database_path().parent / REPORT_NAME
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def load_stress_report() -> dict | None:
    path = Path(database_path().parent / REPORT_NAME)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    report = run_stress()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
