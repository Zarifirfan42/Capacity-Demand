"""Pre-registered metrics. The synthetic windows are an illustration, not evidence."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from app.db import connect, database_path, fetch_all
from app.economics import round_m3, round_rm
from app.engine import product_is_stockable

WATERMARK = "SYNTHETIC ILLUSTRATION, not results"
INFORMAL_PLAN_LABEL = (
    "The synthetic informal plan is the best simple rule on that same noisy book: "
    "internal-first, external-first, earliest required date, penalty and delay per m³, "
    "complete-or-skip, or a greedy rank by unit expected consequence, whichever scores lowest. "
    "The paired figure is the modelled consequence of that rule minus the recommendation. "
    "It is not an observed outcome, and it is not the earliest-date gap."
)


def _illustration_path() -> Path:
    return database_path().parent / "measurement_illustration.json"


def last_value_naive() -> dict:
    with connect() as conn:
        rows = fetch_all(conn, "SELECT plant_id, product_id, month_start, quantity_m3 FROM demand_history ORDER BY month_start")
    series: dict[tuple[int, int], dict[str, float]] = {}
    for row in rows:
        key = (int(row["plant_id"]), int(row["product_id"]))
        bucket = series.setdefault(key, {})
        bucket[row["month_start"]] = bucket.get(row["month_start"], 0.0) + float(row["quantity_m3"])
    errors = []
    for points in series.values():
        ordered = [points[month] for month in sorted(points)]
        for index in range(1, len(ordered)):
            errors.append(ordered[index - 1] - ordered[index])
    mae = sum(abs(error) for error in errors) / len(errors) if errors else 0.0
    return {
        "label": "Diagnostic only. Not a pilot success criterion.",
        "method": "Last-value naive. The forecast for a month is the previous month's actual cubic metres.",
        "sign": "Error = forecast minus actual. A positive error means the forecast ran high.",
        "points": len(errors),
        "mae_m3": round_m3(mae),
    }


def _range(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "min_rm": None, "max_rm": None, "mean_rm": None}
    return {
        "n": len(values),
        "min_rm": round_rm(min(values)),
        "max_rm": round_rm(max(values)),
        "mean_rm": round_rm(sum(values) / len(values)),
    }


def override_learning() -> dict:
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM decisions WHERE status != 'replaced' ORDER BY id")
    ex_ante = []
    realised_override = []
    realised_accepted = []
    for row in rows:
        ex_ante_gap = float(row["consequence_final"]) - float(row["consequence_recommended"])
        if row["status"] == "modified":
            ex_ante.append(ex_ante_gap)
        if not row.get("actual_json"):
            continue
        actual = json.loads(row["actual_json"])
        realised = 0.0
        for line in actual.get("lines", []):
            requested = float(line.get("requested_m3") or 0)
            delivered = float(line.get("delivered_m3") or 0)
            margin = float(line.get("contribution_margin_rm") or 0)
            undelivered = max(0.0, requested - delivered)
            margin_lost = margin * (undelivered / requested) if requested else 0.0
            realised += margin_lost + float(line.get("penalty_paid_rm") or 0) + float(line.get("programme_days_lost") or 0) * float(line.get("delay_cost_per_day_rm") or 0)
        if row["status"] == "modified":
            realised_override.append(realised)
        elif row["status"] == "approved":
            realised_accepted.append(realised)
    return {
        "ex_ante": {
            "definition": "Approved expected consequence minus the recommendation's expected consequence, on the same book.",
            **_range(ex_ante),
            "quote_mean": len(ex_ante) >= 8,
        },
        "realised": {
            "definition": "Realised consequence of overridden decisions beside accepted decisions. No win rate.",
            "overridden": _range(realised_override),
            "accepted": _range(realised_accepted),
            "quote_difference": len(realised_override) >= 8 and len(realised_accepted) >= 8,
            "note": "Below 8 decisions in either group the comparison is noise. The page shows n and the range.",
        },
    }


def _burden(rows: list[dict]) -> dict:
    internal_m3 = external_m3 = internal_rm = external_rm = 0.0
    for row in rows:
        final = json.loads(row["final_json"])
        for line in final.get("allocations", []):
            unserved = float(line.get("unserved_quantity") or 0)
            consequence = float(line.get("expected_consequence_rm") or 0)
            if line.get("demand_type") == "Internal":
                internal_m3 += unserved
                internal_rm += consequence
            else:
                external_m3 += unserved
                external_rm += consequence
    total_m3 = internal_m3 + external_m3
    total_rm = internal_rm + external_rm
    return {
        "n": len(rows),
        "internal_unserved_share": None if total_m3 <= 0 else round(internal_m3 / total_m3, 4),
        "external_unserved_share": None if total_m3 <= 0 else round(external_m3 / total_m3, 4),
        "internal_consequence_share": None if total_rm <= 0 else round(internal_rm / total_rm, 4),
        "external_consequence_share": None if total_rm <= 0 else round(external_rm / total_rm, 4),
        "internal_unserved_m3": round_m3(internal_m3),
        "external_unserved_m3": round_m3(external_m3),
    }


def cash_tied_up(snapshots: list[dict], demands: list[dict], products: list[dict]) -> dict:
    """Excess precast from weekly counts. Ready-mix is omitted. The 1 Oct seed stock is not a snapshot."""
    by_product = {int(row["id"]): row for row in products}
    readings: list[dict[int, float]] = []
    for snap in snapshots:
        product = by_product.get(int(snap["product_id"]))
        if product is None or not product_is_stockable(product):
            continue
        unit = float(product["inventory_value_per_m3"])
        as_of = date.fromisoformat(str(snap["as_of_date"])[:10])
        on_hand = float(snap["on_hand"])
        safety = float(snap["safety_stock"])
        windows: dict[int, float] = {}
        for days in (30, 60, 90):
            end = (as_of + timedelta(days=days - 1)).isoformat()
            forward = sum(
                float(row["requested_quantity"])
                for row in demands
                if int(row["plant_id"]) == int(snap["plant_id"])
                and int(row["product_id"]) == int(snap["product_id"])
                and as_of.isoformat() <= str(row["required_date"])[:10] <= end
            )
            excess = max(0.0, on_hand - safety - forward)
            windows[days] = excess * unit
        readings.append(windows)

    def mean(days: int) -> float | None:
        if not readings:
            return None
        return round_rm(sum(row[days] for row in readings) / len(readings))

    return {
        "n": len(readings),
        "mean_30_rm": mean(30),
        "mean_60_rm": mean(60),
        "mean_90_rm": mean(90),
        "definition": "Weekly stock count: max(0, on-hand − safety stock − precast demand due in the next 30, 60, or 90 days) × assumed value per m³. Ready-mix is omitted.",
    }


def live_metrics() -> dict:
    with connect() as conn:
        plans = fetch_all(conn, "SELECT * FROM informal_plans ORDER BY id")
        decisions = fetch_all(conn, "SELECT * FROM decisions WHERE status != 'replaced' ORDER BY id")
        snapshots = fetch_all(conn, "SELECT * FROM inventory_snapshots ORDER BY as_of_date")
        expedites = fetch_all(conn, "SELECT * FROM expedite_decisions ORDER BY id")
        demands = fetch_all(
            conn,
            "SELECT plant_id, product_id, required_date, requested_quantity, economics_basis FROM demands",
        )
        incomplete_n = sum(1 for row in demands if row.get("economics_basis") == "default_assumption")
        demands = [row for row in demands if row.get("economics_basis") != "default_assumption"]
        products = fetch_all(conn, "SELECT * FROM products")
    paired = [float(row["paired_gap_rm"]) for row in plans]
    cash_paid = 0.0
    programme_days = 0.0
    actual_n = 0
    for row in decisions:
        if not row.get("actual_json"):
            continue
        actual_n += 1
        actual = json.loads(row["actual_json"])
        for line in actual.get("lines", []):
            cash_paid += float(line.get("penalty_paid_rm") or 0)
            programme_days += float(line.get("programme_days_lost") or 0)
    modified = sum(1 for row in decisions if row["status"] == "modified")
    return {
        "paired_shadow": {
            "n": len(paired),
            "mean_gap_rm": None if not paired else round_rm(sum(paired) / len(paired)),
            "minimum_sample": 8,
            "inconclusive": len(paired) < 8,
            "definition": "Modelled consequence of the informal plan minus the modelled recommendation, on the same book. Not an observed outcome. Realised outcomes come from actuals.",
        },
        "cash_paid_rm": round_rm(cash_paid),
        "programme_days_lost": round(programme_days, 2),
        "actual_decisions": actual_n,
        "override_share": {
            "modified": modified,
            "decisions": len(decisions),
            "quote_percentage": len(decisions) >= 8,
        },
        "burden": _burden(decisions),
        "snapshots": len(snapshots),
        "cash_tied_up": cash_tied_up(snapshots, demands, products),
        "expedite": {
            "n": len(expedites),
            "approved": sum(1 for row in expedites if row["decision"] == "approve"),
            "declined": sum(1 for row in expedites if row["decision"] == "decline"),
            "estimated_avoided_rm": round_rm(sum(float(row["estimated_avoided_rm"] or 0) for row in expedites)),
            "label": "estimated avoided",
        },
        "forecast_diagnostic": last_value_naive(),
        "override_learning": override_learning(),
        "default_margin_lines_excluded": incomplete_n,
    }


def score_illustration(weeks: list[dict]) -> dict:
    """Apply the pre-registered rules to an illustrated week list. Not evidence."""
    baseline = [row for row in weeks if row["window"] == "baseline"]
    pilot = [row for row in weeks if row["window"] == "pilot"]
    paired = [float(row["paired_gap_rm"]) for row in weeks if row.get("paired_gap_rm") is not None]
    mean_gap = sum(paired) / len(paired) if paired else 0.0

    def per_day(rows: list[dict], key: str) -> float | None:
        days = sum(int(row["constrained_days"]) for row in rows)
        if days <= 0:
            return None
        return sum(float(row[key]) for row in rows) / days

    base_days = per_day(baseline, "damages_rm")
    pilot_days = per_day(pilot, "damages_rm")
    failure = None
    if len(paired) >= 8 and mean_gap < 0:
        failure = "Paired shadow gap is below RM0 with at least 8 pairs."
    if base_days is not None and pilot_days is not None and len(baseline) >= 4 and len(pilot) >= 4:
        if pilot_days > base_days * 1.10:
            failure = "Realised damages per constrained day moved the wrong way by more than 10%."
    return {
        "watermark": WATERMARK,
        "paired_mean_gap_rm": round_rm(mean_gap),
        "paired_n": len(paired),
        "baseline_weeks": len(baseline),
        "pilot_weeks": len(pilot),
        "damages_per_constrained_day_baseline_rm": None if base_days is None else round_rm(base_days),
        "damages_per_constrained_day_pilot_rm": None if pilot_days is None else round_rm(pilot_days),
        "failure_rule_fired": failure,
        "inconclusive": len(paired) < 8 or len(baseline) < 4 or len(pilot) < 4,
    }


def weeks_from_solves(solved_months: list[dict]) -> list[dict]:
    """Turn solved months into illustrated weeks. The gap is the best-simple-rule gap."""
    rows = []
    for index, solved in enumerate(solved_months):
        recommended = float(solved["recommended_rm"])
        gap = max(0.0, float(solved["best_rule_gap_rm"]))
        window = "baseline" if index < 4 else "pilot"
        rows.append(
            {
                "week": index + 1,
                "window": window,
                "paired_gap_rm": round_rm(gap),
                "damages_rm": round_rm(recommended + (gap if window == "baseline" else 0.0)),
                "constrained_days": int(solved.get("constrained_days") or 0),
                "programme_days": float(solved.get("programme_days") or 0),
            }
        )
    return rows


def illustrate_failure(weeks: list[dict]) -> list[dict]:
    """Copy a solved illustration and push the pilot the wrong way, so a failure rule can fire."""
    rows = [dict(row) for row in weeks]
    baseline = [row for row in rows if row["window"] == "baseline"]
    pilot = [row for row in rows if row["window"] == "pilot"]
    base_days = sum(int(row["constrained_days"]) for row in baseline) or 1
    base_rate = sum(float(row["damages_rm"]) for row in baseline) / base_days
    base_gap = sum(float(row["paired_gap_rm"]) for row in baseline)
    pilot_gap = -((base_gap + 500.0 * max(len(pilot), 1)) / max(len(pilot), 1)) - 500.0
    for row in pilot:
        row["paired_gap_rm"] = round_rm(pilot_gap)
        row["damages_rm"] = round_rm(base_rate * 1.4 * int(row["constrained_days"] or 1))
    return rows


def build_illustration() -> dict:
    """Twelve stress months. The informal plan is the best simple rule on each month."""
    import random

    from app.engine import allocate_world, load_world
    from app.stress import synthetic_month

    rng = random.Random(202610)
    base = load_world()
    solved_months = []
    for _ in range(12):
        world = synthetic_month(base, rng)
        result = allocate_world(world, include_comparison=False, include_expedite=False, lex=False)
        totals = result["totals"]
        solved_months.append(
            {
                "recommended_rm": float(totals["expected_consequence_rm"]),
                "best_rule_gap_rm": float(totals["value_protected_vs_best_rule_rm"]),
                "constrained_days": 4 if float(totals["dated_shortfall_m3"]) > 0.5 else 1,
                "programme_days": float(totals.get("programme_days") or 0),
            }
        )
    success_weeks = weeks_from_solves(solved_months)
    failure_weeks = illustrate_failure(success_weeks)
    payload = {
        "watermark": WATERMARK,
        "off_control_tower": True,
        "informal_plan": INFORMAL_PLAN_LABEL,
        "method": (
            "Each of 12 illustrated weeks is a stress-simulator month: utilisation, quantities, dates, and contract types are redrawn, then the book is solved. "
            + INFORMAL_PLAN_LABEL
            + " The second run keeps those solves and then sets the pilot gap below RM0 and the pilot damages per constrained day 40% above the baseline rate, so a failure rule fires."
        ),
        "success": {**score_illustration(success_weeks), "weeks": success_weeks},
        "failure": {**score_illustration(failure_weeks), "weeks": failure_weeks},
    }
    path = _illustration_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    payload = build_illustration()
    print(payload["watermark"])
    print("success mean", payload["success"].get("paired_mean_gap_rm"))
    print("success failure", payload["success"].get("failure_rule_fired"))
    print("failure rule", payload["failure"].get("failure_rule_fired"))


if __name__ == "__main__":
    main()


def load_illustration() -> dict:
    path = _illustration_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "watermark": WATERMARK,
        "missing": True,
        "note": "Run python -m app.measurement to write the synthetic illustration. It is not evidence.",
    }


def build_measurement() -> dict:
    return {
        "live": live_metrics(),
        "illustration": load_illustration(),
        "rollout": "Pre-registered stagger: Shah Alam Works in pilot, Pasir Gudang Works remaining in shadow as the control. Difference in differences uses the secondary per-constrained-day outcomes.",
        "primary": "The primary measure is the modelled consequence of the informal plan minus the modelled recommendation, on the same book. Realised outcomes come later from actuals and are shown per constrained day.",
    }
