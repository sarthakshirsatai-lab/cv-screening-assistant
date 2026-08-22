"""Deterministic rule engine. No LLM calls, no I/O beyond the data it's given.

Every Met/Partial/Gap result and the overall fit band come from explicit code
logic below -- nothing here is a black box, and nothing here can reject a
candidate on its own (that decision belongs to a human in the UI).
"""
import re

POINTS = {"Met": 3, "Partial": 1, "Gap": 0}
MAX_SCORE = 21

STRONG_FIT_MIN = 17
NEEDS_REVIEW_MIN = 9

WAREHOUSE_TERMS = ["warehouse", "distribution", "fulfillment", "logistics"]
FORKLIFT_CERT_TERMS = ["forklift", "powered industrial truck", "pit license"]
RF_WMS_TERMS = ["rf scanner", "wms", "sap wms", "voice pick"]
INVENTORY_TERMS = ["inventory", "order picking", "pick and pack", "cycle count", "order fulfillment"]
EDUCATION_RECOGNIZED = [
    "high school diploma", "ged", "associate degree", "associate's degree",
    "bachelor degree", "bachelor's degree", "some college", "high school",
]
SHIFT_POSITIVE = ["weekend", "flexible", "any shift", "overnight", "nights", "night shift"]
SHIFT_RESTRICTIVE = ["weekdays only", "day shift only", "no weekends", "no overnight", "monday-friday only"]

LIFT_WEIGHT_RE = re.compile(r"lift\w*\s+(?:up to\s+)?(\d{2,3})\s*(?:lbs|pounds)", re.I)


def _work_history_text(entry: dict) -> str:
    return f"{entry.get('title') or ''} {entry.get('description') or ''}".lower()


def _corroborated(keyword: str, extracted: dict) -> bool:
    """True only if `keyword` shows up in an actual work-history entry --
    a bare mention in skills_keywords/equipment_experience never counts."""
    kw = keyword.lower()
    for entry in extracted.get("work_history") or []:
        if kw in _work_history_text(entry):
            return True
    return False


def _any_corroborated(keywords: list, extracted: dict) -> bool:
    return any(_corroborated(kw, extracted) for kw in keywords)


def _mentioned_in_list(keywords: list, values: list) -> bool:
    values_lower = [str(v).lower() for v in (values or [])]
    return any(any(kw in v for v in values_lower) for kw in keywords)


def _score_years_experience(extracted: dict) -> tuple:
    total_years = extracted.get("total_years_experience") or 0
    corroborated_years = 0.0
    for entry in extracted.get("work_history") or []:
        text = _work_history_text(entry)
        if any(term in text for term in WAREHOUSE_TERMS):
            corroborated_years += entry.get("duration_years") or 0

    if total_years >= 2 and corroborated_years >= 1.5:
        return "Met", f"{total_years} yrs total experience, {corroborated_years:.1f} yrs corroborated in warehouse/distribution roles."
    if total_years >= 0.5:
        return "Partial", f"Only {total_years} yrs total experience, or corroboration ({corroborated_years:.1f} yrs) below 2-year bar."
    return "Gap", "No meaningful warehouse/distribution experience found."


def _score_forklift_cert(extracted: dict) -> tuple:
    certifications = extracted.get("certifications") or []
    if _mentioned_in_list(FORKLIFT_CERT_TERMS, certifications):
        return "Met", "Named forklift/PIT certification listed."
    if _corroborated("forklift", extracted):
        return "Met", "Forklift use corroborated by work history."
    if _mentioned_in_list(FORKLIFT_CERT_TERMS, extracted.get("equipment_experience")) or \
       _mentioned_in_list(FORKLIFT_CERT_TERMS, extracted.get("skills_keywords")):
        return "Partial", "Forklift mentioned only in a skills/equipment list, not corroborated by work history."
    return "Gap", "No forklift certification or experience mentioned."


def _score_rf_wms_experience(extracted: dict) -> tuple:
    if _any_corroborated(RF_WMS_TERMS, extracted):
        return "Met", "RF scanner/WMS use corroborated by work history."
    if _mentioned_in_list(RF_WMS_TERMS, extracted.get("equipment_experience")) or \
       _mentioned_in_list(RF_WMS_TERMS, extracted.get("skills_keywords")):
        return "Partial", "RF scanner/WMS listed as a skill but not corroborated by work history."
    return "Gap", "No RF scanner/WMS experience mentioned."


def _score_lift_50lbs(extracted: dict, raw_text: str) -> tuple:
    mentioned = bool(extracted.get("physical_capability_mentioned"))
    match = LIFT_WEIGHT_RE.search(raw_text or "")
    if mentioned and match and int(match.group(1)) >= 40:
        return "Met", f"Explicit lifting capability of {match.group(1)} lbs stated."
    if mentioned:
        return "Partial", "General physical-capability language present, but no specific weight figure found."
    return "Gap", "No lifting/physical-capability statement found."


def _score_education_level(extracted: dict) -> tuple:
    edu = (extracted.get("education_level") or "").strip().lower()
    if not edu:
        return "Gap", "No education level stated."
    if any(r in edu for r in EDUCATION_RECOGNIZED):
        return "Met", f"Education level '{extracted['education_level']}' meets the HS diploma/GED minimum."
    return "Partial", f"Education level '{extracted['education_level']}' stated but not a recognized credential."


def _score_shift_availability(extracted: dict) -> tuple:
    avail = (extracted.get("shift_availability") or "").strip().lower()
    if not avail:
        return "Gap", "No shift/availability information stated."
    if any(r in avail for r in SHIFT_RESTRICTIVE):
        return "Partial", f"Availability '{extracted['shift_availability']}' is restricted."
    if any(p in avail for p in SHIFT_POSITIVE):
        return "Met", f"Availability '{extracted['shift_availability']}' covers weekend/flexible shifts."
    return "Partial", f"Availability '{extracted['shift_availability']}' stated but doesn't clearly cover weekend/flexible shifts."


def _score_inventory_picking(extracted: dict) -> tuple:
    for entry in extracted.get("work_history") or []:
        text = _work_history_text(entry)
        if any(term in text for term in INVENTORY_TERMS):
            if entry.get("duration_years"):
                return "Met", "Inventory/order-picking experience corroborated by a timed work-history entry."
            return "Partial", "Inventory/order-picking mentioned in work history but without a clear duration."
    if _mentioned_in_list(INVENTORY_TERMS, extracted.get("skills_keywords")):
        return "Partial", "Inventory/order-picking listed only as a skill, not corroborated by work history."
    return "Gap", "No inventory management/order-picking experience mentioned."


_SCORERS = [
    ("years_experience", lambda ext, raw: _score_years_experience(ext)),
    ("forklift_cert", lambda ext, raw: _score_forklift_cert(ext)),
    ("rf_wms_experience", lambda ext, raw: _score_rf_wms_experience(ext)),
    ("lift_50lbs", lambda ext, raw: _score_lift_50lbs(ext, raw)),
    ("education_level", lambda ext, raw: _score_education_level(ext)),
    ("shift_availability", lambda ext, raw: _score_shift_availability(ext)),
    ("inventory_picking", lambda ext, raw: _score_inventory_picking(ext)),
]


def fit_band_for(total_score: int) -> str:
    if total_score >= STRONG_FIT_MIN:
        return "Strong Fit"
    if total_score >= NEEDS_REVIEW_MIN:
        return "Needs Review"
    return "Likely Not a Fit"


def score_candidate(extracted: dict, raw_text: str, jd_requirements: list) -> dict:
    labels = {r["criterion_key"]: r["criterion_label"] for r in jd_requirements}
    criterion_scores = []
    total_score = 0

    for key, scorer in _SCORERS:
        result, rationale = scorer(extracted, raw_text)
        points = POINTS[result]
        total_score += points
        criterion_scores.append({
            "criterion_key": key,
            "criterion_label": labels.get(key, key),
            "result": result,
            "points_awarded": points,
            "points_possible": POINTS["Met"],
            "rationale": rationale,
        })

    return {
        "criterion_scores": criterion_scores,
        "total_score": total_score,
        "max_score": MAX_SCORE,
        "fit_band": fit_band_for(total_score),
    }
