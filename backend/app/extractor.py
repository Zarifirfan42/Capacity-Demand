"""Prepare a demand line from unstructured text.

This is a rules-based stand-in for OCR and document extraction. It classifies
and structures a demand record. It does not allocate capacity.
"""

from __future__ import annotations

import re

from app.economics import HORIZON_END, HORIZON_START

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

SAMPLE_OCR = """SCANNED PURCHASE ORDER  (simulated OCR transcript)
Supplier: Shah Alam Works
Buyer: Gamuda Engineering Sdn Bhd
Material: Ready-mix Grade 40
Quantity: 120 m3
Required on site: 9 Oct 2026
Site: Gamuda depot extension, Shah Alam
Contribution margin RM 9,600
Contractual penalty RM 40,000 if the pour is missed
Status: Confirmed PO
Customer type: Main contractor
"""

SAMPLE_EMAIL = """From: planning@elmina.internal
Subject: Additional internal pour

Internal project: Elmina gatehouse
Please book 80 m3 Grade 40 at Shah Alam Works by 12 Oct 2026.
This is probable, not a firm release.
Programme delay if missed: 1 day at RM 6,000 per day.
Transfer margin RM 2,400.
Criticality: Medium.
"""


def _money(text: str, label: str) -> float | None:
    match = re.search(label + r".{0,50}?RM\s*([\d,]+(?:\.\d+)?)", text, re.I | re.S)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _date(text: str) -> str | None:
    iso = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    named = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", text)
    if named:
        month = MONTHS.get(named.group(2).lower())
        if month:
            return f"{named.group(3)}-{month:02d}-{int(named.group(1)):02d}"
    slash = re.search(r"(\d{1,2})/(\d{1,2})/(20\d{2})", text)
    if slash:
        return f"{slash.group(3)}-{int(slash.group(2)):02d}-{int(slash.group(1)):02d}"
    return None


def _quantity(text: str) -> float | None:
    match = re.search(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*m(?:3|³)", text, re.I)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _classify(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    internal_hits = [phrase for phrase in ("internal project", "group project", "our programme", "our program", "internal pour") if phrase in lowered]
    external_hits = [phrase for phrase in ("purchase order", "confirmed po", " sdn", "bhd", "customer type", "buyer:") if phrase in lowered]
    if internal_hits and not external_hits:
        return "Internal", "Group project", f"Classified as Internal because the text says '{internal_hits[0]}'."
    if external_hits and not internal_hits:
        return "External", "Main contractor", f"Classified as External because the text says '{external_hits[0]}'."
    if "government" in lowered or "jkr" in lowered:
        return "External", "Government", "Classified as External because the text refers to a government buyer."
    if "developer" in lowered:
        return "External", "Developer", "Classified as External because the text refers to a developer."
    if "project" in lowered:
        return "Internal", "Group project", "Classified as Internal because the text refers to a project and not a customer PO."
    return "External", "Main contractor", "Classified as External by default. The text has no internal programme language. Review this before it enters the demand book."


def _confidence(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    if "forecast" in lowered:
        return "Forecast", "Forecast", "Confidence set to Forecast because the text says forecast."
    if "probable" in lowered or "not a firm" in lowered:
        return "Probable", "Open", "Confidence set to Probable because the release is not firm."
    if any(phrase in lowered for phrase in ("confirmed", "purchase order", " po", "firm")):
        return "Confirmed", "Firm", "Confidence set to Confirmed because the text refers to a firm order or PO."
    return "Probable", "Open", "Confidence set to Probable. The text does not say confirmed, forecast, or probable explicitly."


def _customer(text: str, demand_type: str) -> str:
    labelled = re.search(r"(?:internal project|project|site|buyer)\s*:\s*(.+)", text, re.I)
    if labelled:
        return labelled.group(1).strip().split("\n")[0][:80]
    for_the = re.search(r"for the\s+([A-Za-z0-9][A-Za-z0-9 &'./-]{2,60})", text, re.I)
    if for_the:
        return for_the.group(1).strip()
    if demand_type == "Internal":
        return "Unnamed internal project"
    return "Unnamed external customer"


def _match_plant(text: str, plants: list[dict]) -> tuple[dict | None, str]:
    lowered = text.lower()
    for plant in plants:
        if plant["name"].lower() in lowered or plant["code"].lower() in lowered:
            return plant, f"Plant matched on '{plant['name']}'."
        if plant["location"].lower() in lowered:
            return plant, f"Plant matched on location '{plant['location']}'."
    if "shah alam" in lowered:
        found = next((plant for plant in plants if "Shah Alam" in plant["name"]), None)
        return found, "Plant matched on Shah Alam."
    if "pasir gudang" in lowered or "johor" in lowered:
        found = next((plant for plant in plants if "Pasir Gudang" in plant["name"]), None)
        return found, "Plant matched on Pasir Gudang."
    return None, "Plant was not found. Choose a plant before adding this line."


def _match_product(text: str, products: list[dict]) -> tuple[dict | None, str]:
    lowered = text.lower()
    if "grade 50" in lowered or "g50" in lowered:
        found = next((product for product in products if product["code"] == "G50"), None)
        return found, "Product matched on Grade 50."
    if "grade 40" in lowered or "g40" in lowered:
        found = next((product for product in products if product["code"] == "G40"), None)
        return found, "Product matched on Grade 40."
    if "precast" in lowered or "wall panel" in lowered or "facade panel" in lowered:
        found = next((product for product in products if product["code"] == "PCS"), None)
        return found, "Product matched on precast panels."
    return None, "Product was not found. Choose a product before adding this line."


def _criticality(text: str, demand_type: str) -> str:
    lowered = text.lower()
    for level in ("Critical", "High", "Medium", "Low"):
        if re.search(rf"criticality\s*:\s*{level}", lowered, re.I):
            return level
    if demand_type == "External":
        return "n/a"
    if "critical path" in lowered or "critical" in lowered:
        return "Critical"
    return "Medium"


def _delay(text: str) -> tuple[float, float]:
    days = 0.0
    per_day = 0.0
    day_match = re.search(r"(\d+(?:\.\d+)?)\s*days?", text, re.I)
    if day_match and "day" in text.lower():
        days = float(day_match.group(1))
    cost_match = re.search(r"RM\s*([\d,]+(?:\.\d+)?)\s*per\s*day", text, re.I)
    if cost_match:
        per_day = float(cost_match.group(1).replace(",", ""))
    return days, per_day


def extract_demand(text: str, plants: list[dict], products: list[dict], source: str) -> dict:
    """Turn raw text into a draft demand line. The caller must confirm it."""
    cleaned = text.strip()
    warnings: list[str] = []
    steps: list[str] = [
        "The allocation engine is not used here. This step only prepares a demand line for a person to accept.",
    ]
    if not cleaned:
        return {"ok": False, "warnings": ["Paste a document, email, or OCR transcript first."], "draft": None, "steps": steps}

    demand_type, customer_type, type_reason = _classify(cleaned)
    steps.append(type_reason)
    confidence, status, confidence_reason = _confidence(cleaned)
    steps.append(confidence_reason)
    plant, plant_reason = _match_plant(cleaned, plants)
    steps.append(plant_reason)
    product, product_reason = _match_product(cleaned, products)
    steps.append(product_reason)
    quantity = _quantity(cleaned)
    required = _date(cleaned)
    if quantity is None:
        warnings.append("Quantity in m³ was not found.")
    else:
        steps.append(f"Quantity read as {quantity:g} m³.")
    if required is None:
        warnings.append("Required date was not found.")
    elif required < HORIZON_START or required > HORIZON_END:
        warnings.append(f"Required date {required} is outside the prototype horizon {HORIZON_START} to {HORIZON_END}.")
    else:
        steps.append(f"Required date read as {required}.")
    margin = _money(cleaned, r"margin")
    penalty = _money(cleaned, r"penalt")
    if margin is None:
        margin = 0.0
        warnings.append("Contribution margin was not found and is set to RM0. Edit it if you know the value.")
    else:
        steps.append(f"Contribution margin read as RM{margin:,.0f}.")
    if penalty is None:
        penalty = 0.0
    else:
        steps.append(f"Contractual penalty read as RM{penalty:,.0f}.")
    delay_days, delay_cost = _delay(cleaned)
    if delay_days:
        steps.append(f"Programme delay read as {delay_days:g} days at RM{delay_cost:,.0f} per day.")
    customer = _customer(cleaned, demand_type)
    type_match = re.search(r"customer type\s*:\s*(.+)", cleaned, re.I)
    if type_match:
        customer_type = type_match.group(1).strip().split("\n")[0][:60]
        steps.append(f"Customer type read as {customer_type}.")
    criticality = _criticality(cleaned, demand_type)
    if plant is None or product is None or quantity is None or required is None:
        ok = False
    elif required < HORIZON_START or required > HORIZON_END:
        ok = False
    else:
        ok = True
    draft = {
        "demand_type": demand_type,
        "customer_or_project": customer,
        "customer_type": customer_type,
        "plant_id": None if plant is None else plant["id"],
        "plant_name": None if plant is None else plant["name"],
        "product_id": None if product is None else product["id"],
        "product_name": None if product is None else product["name"],
        "required_date": required,
        "requested_quantity": quantity,
        "confirmed_quantity": quantity if confidence == "Confirmed" and quantity else 0,
        "demand_status": status,
        "confidence_level": confidence,
        "contribution_margin": margin,
        "contractual_penalty": penalty,
        "project_criticality": criticality,
        "delay_days_if_unserved": delay_days,
        "delay_cost_per_day": delay_cost,
        "source": source,
        "notes": "Prepared from unstructured text. A person must confirm this line before it affects allocation.",
        "validation_status": "Pending",
    }
    signals = [
        quantity is not None,
        required is not None,
        plant is not None,
        product is not None,
        margin > 0,
        bool(customer) and not customer.startswith("Unnamed"),
    ]
    extraction_confidence = round(sum(1 for signal in signals if signal) / len(signals), 2)
    draft["extraction_confidence"] = extraction_confidence
    steps.append(
        f"Extraction confidence {extraction_confidence:.0%}. This is how many of the core fields were read, not a model probability. "
        "The line stays out of the allocation book until a person adds it."
    )
    return {
        "ok": ok,
        "warnings": warnings,
        "draft": draft,
        "steps": steps,
        "source_label": source,
        "extraction_confidence": extraction_confidence,
        "validation_status": "Pending",
    }
