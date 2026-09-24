"""A supporting forecast for the synthetic history. It does not enter the allocation book.

History is monthly, October 2025 through September 2026. October 2026 orders in
the demand book are a separate committed view. The forecast is not a confirmed order.
"""

from __future__ import annotations

from datetime import date

from app.db import connect, fetch_all
from app.economics import round_m3

# Construction demand in this illustration is softer in the monsoon months and
# stronger in the drier months. These indexes are synthetic.
SEASONAL = {
    1: 0.85,
    2: 0.88,
    3: 1.05,
    4: 1.08,
    5: 1.10,
    6: 1.06,
    7: 1.02,
    8: 1.04,
    9: 1.00,
    10: 0.96,
    11: 0.82,
    12: 0.80,
}

# Monthly base before season, trend, spike, and dip. Synthetic m³.
BASE = {
    (1, 1): 1400.0,
    (1, 2): 280.0,
    (1, 3): 90.0,
    (2, 1): 700.0,
    (2, 2): 240.0,
    (2, 3): 50.0,
}
INTERNAL_SHARE = 0.42


def history_months() -> list[date]:
    months = []
    year, month = 2025, 10
    for _ in range(12):
        months.append(date(year, month, 1))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return months


def history_quantity(plant_id: int, product_id: int, month: date, index: int) -> float:
    base = BASE[(plant_id, product_id)]
    seasonal = SEASONAL[month.month]
    trend = 1 + (0.006 * index)
    quantity = base * seasonal * trend
    if plant_id == 1 and product_id == 1 and month.month in (3, 4) and month.year == 2026:
        quantity *= 1.25
    if plant_id == 2 and product_id == 2 and month.year == 2026 and month.month == 1:
        quantity *= 0.80
    return round(quantity, 1)


def history_rows() -> list[tuple]:
    rows = []
    for index, month in enumerate(history_months()):
        for plant_id, product_id in BASE:
            total = history_quantity(plant_id, product_id, month, index)
            internal = round(total * INTERNAL_SHARE, 1)
            external = round(total - internal, 1)
            stamp = month.isoformat()
            rows.append((plant_id, product_id, "Internal", stamp, internal))
            rows.append((plant_id, product_id, "External", stamp, external))
    return rows


def _series(rows: list[dict]) -> dict[tuple[int, int], list[tuple[str, float]]]:
    grouped: dict[tuple[int, int], dict[str, float]] = {}
    for row in rows:
        key = (int(row["plant_id"]), int(row["product_id"]))
        grouped.setdefault(key, {})
        month = row["month_start"]
        grouped[key][month] = grouped[key].get(month, 0.0) + float(row["quantity_m3"])
    return {key: sorted(values.items()) for key, values in grouped.items()}


def _metrics(errors: list[float], actuals: list[float]) -> dict:
    n = len(errors)
    mae = sum(abs(error) for error in errors) / n
    rmse = (sum(error * error for error in errors) / n) ** 0.5
    bias = sum(errors) / n
    mape_pairs = [(abs(error) / actual, actual) for error, actual in zip(errors, actuals) if actual >= 20]
    mape = None if not mape_pairs else sum(pair[0] for pair in mape_pairs) / len(mape_pairs)
    return {
        "points": n,
        "mae_m3": round_m3(mae),
        "rmse_m3": round_m3(rmse),
        "bias_m3": round_m3(bias),
        "mape": None if mape is None else round(mape, 4),
        "mape_note": "MAPE uses only months whose actual is at least 20 m³, so a near-zero month cannot dominate the percentage.",
        "bias_meaning": "Forecast minus actual. A positive bias means the forecast ran high.",
    }


def build_forecast() -> dict:
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM demand_history ORDER BY month_start, plant_id, product_id")
        adjustments = fetch_all(conn, "SELECT * FROM forecast_adjustments")
        plants = {row["id"]: row["name"] for row in fetch_all(conn, "SELECT id, name FROM plants")}
        products = {row["id"]: row["name"] for row in fetch_all(conn, "SELECT id, name FROM products")}
    series = _series(rows)
    backtest_errors: list[float] = []
    backtest_actuals: list[float] = []
    lines = []
    for (plant_id, product_id), points in sorted(series.items()):
        quantities = [qty for _, qty in points]
        for index in range(3, len(quantities)):
            forecast = sum(quantities[index - 3:index]) / 3
            backtest_errors.append(forecast - quantities[index])
            backtest_actuals.append(quantities[index])
        recent = quantities[-3:]
        recent_months = [date.fromisoformat(month) for month, _ in points[-3:]]
        seasonal_recent = sum(SEASONAL[month.month] for month in recent_months) / 3
        statistical = (sum(recent) / 3) * (SEASONAL[10] / seasonal_recent)
        adjustment = next(
            (
                float(row["adjustment_m3"])
                for row in adjustments
                if row["plant_id"] == plant_id and row["product_id"] == product_id and row["month_start"] == "2026-10-01"
            ),
            0.0,
        )
        lines.append(
            {
                "plant_id": plant_id,
                "product_id": product_id,
                "plant_name": plants[plant_id],
                "product_name": products[product_id],
                "history_m3": [{"month": month, "actual_m3": qty} for month, qty in points],
                "statistical_forecast_m3": round_m3(statistical),
                "planner_adjustment_m3": round_m3(adjustment),
                "planning_forecast_m3": round_m3(max(0.0, statistical + adjustment)),
            }
        )
    return {
        "label": "Synthetic history, October 2025–September 2026. Not Chin Hin actuals.",
        "method": (
            "Backtest: a three-month moving average, scored from the fourth history month onward. "
            "October 2026 planning forecast: that average, rescaled by the October seasonal index relative to the last three months. "
            "A planner adjustment is added only when someone records one. The result is not written into the order book."
        ),
        "evaluation": _metrics(backtest_errors, backtest_actuals) if backtest_errors else None,
        "lines": lines,
        "october_planning_forecast_m3": round_m3(sum(line["planning_forecast_m3"] for line in lines)),
        "not_confirmed": "This forecast is a planning signal. It is not a confirmed order and it is not passed to the allocator unless a person adds a forecast-class demand line.",
        "october_weeks": _october_weeks(),
    }


def _october_weeks() -> list[dict]:
    """Confirmed book versus other book lines, by week. The statistical forecast is not added in."""
    with connect() as conn:
        rows = fetch_all(conn, "SELECT required_date, requested_quantity, confidence_level FROM demands")
    weeks = []
    start = date(2026, 10, 1)
    for offset in range(0, 30, 7):
        begin = date.fromordinal(start.toordinal() + offset)
        end = date.fromordinal(min(begin.toordinal() + 6, date(2026, 10, 30).toordinal()))
        confirmed = 0.0
        other = 0.0
        for row in rows:
            due = date.fromisoformat(row["required_date"])
            if due < begin or due > end:
                continue
            qty = float(row["requested_quantity"])
            if row["confidence_level"] == "Confirmed":
                confirmed += qty
            else:
                other += qty
        weeks.append(
            {
                "week": f"{begin.isoformat()} to {end.isoformat()}",
                "confirmed_m3": round_m3(confirmed),
                "forecast_class_in_book_m3": round_m3(other),
                "planning_demand_m3": round_m3(confirmed + other),
            }
        )
    return weeks
