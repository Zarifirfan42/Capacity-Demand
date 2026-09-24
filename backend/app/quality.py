"""Checks that run before anyone treats the book as ready to allocate.

The score is the share of checks that passed. Each failure is listed, so the
percentage is not a separate opinion.
"""

from __future__ import annotations

from app.economics import HORIZON_END, HORIZON_START
from app.engine import load_world

CONFIDENCE_LEVELS = {"Confirmed", "Probable", "Forecast"}


def _issue(code: str, message: str, count: int = 1) -> dict:
    return {"code": code, "message": message, "count": count}


def assess(world: dict | None = None) -> dict:
    world = world or load_world()
    issues: list[dict] = []
    checks = 0
    failed = 0

    def check(ok: bool, code: str, message: str) -> None:
        nonlocal checks, failed
        checks += 1
        if not ok:
            failed += 1
            issues.append(_issue(code, message))

    plant_ids = {row["id"] for row in world["plants"]}
    product_ids = {row["id"] for row in world["products"]}
    seen_codes: set[str] = set()
    fingerprints: dict[tuple, list[str]] = {}

    for demand in world["demands"]:
        code = str(demand["demand_code"])
        name = str(demand["customer_or_project"])
        check(bool(demand.get("required_date")), "missing_date", f"{code}: required date is missing.")
        required = str(demand.get("required_date") or "")
        if required:
            check(
                HORIZON_START <= required <= HORIZON_END,
                "date_outside_horizon",
                f"{code}: required date {required} is outside {HORIZON_START} to {HORIZON_END}.",
            )
        check(demand.get("plant_id") in plant_ids, "missing_plant", f"{code}: plant is not in the plant list.")
        check(demand.get("product_id") in product_ids, "missing_product", f"{code}: product is not in the product list.")
        qty = float(demand.get("requested_quantity") or 0)
        check(qty > 0, "invalid_quantity", f"{code}: requested quantity must be greater than zero.")
        confirmed = float(demand.get("confirmed_quantity") or 0)
        check(confirmed <= qty + 0.01, "confirmed_above_requested", f"{code}: confirmed quantity is above requested quantity.")
        check(
            demand.get("confidence_level") in CONFIDENCE_LEVELS,
            "unknown_confidence",
            f"{code}: confidence must be Confirmed, Probable, or Forecast.",
        )
        check(code not in seen_codes, "duplicate_code", f"{code}: demand code is duplicated.")
        seen_codes.add(code)
        fingerprint = (
            demand.get("plant_id"),
            demand.get("product_id"),
            required,
            name.strip().lower(),
        )
        fingerprints.setdefault(fingerprint, []).append(code)

    for codes in fingerprints.values():
        if len(codes) > 1:
            check(False, "duplicate_order", f"Same plant, product, date, and name appear on {', '.join(codes)}.")

    for row in world["calendar"]:
        label = f"{row['plant_id']}/{row['product_id']} {row['prod_date']}"
        daily = float(row["daily_capacity"])
        planned = float(row["planned_production"])
        check(daily >= 0 and planned >= 0, "negative_capacity", f"{label}: capacity or planned production is negative.")
        check(planned <= daily + 0.01, "planned_above_capacity", f"{label}: planned production is above daily capacity.")

    for row in world["inventory"]:
        label = f"plant {row['plant_id']} product {row['product_id']}"
        check(float(row["on_hand"]) >= 0, "negative_inventory", f"{label}: on-hand inventory is negative.")
        check(float(row["safety_stock"]) >= 0, "negative_safety", f"{label}: safety stock is negative.")

    passed = checks - failed
    percent = round(100.0 * passed / checks, 1) if checks else 100.0
    return {
        "checks": checks,
        "passed": passed,
        "failed": failed,
        "quality_percent": percent,
        "quality_meaning": "Share of validation checks that passed. It is not a judgement of commercial importance.",
        "issues": issues,
        "synthetic_note": "These checks run on the demonstration book. They do not certify Chin Hin operational data.",
    }
