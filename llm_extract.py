"""LLM call #1: parse a raw CV into structured fields (extraction only, no
judgment). Falls back to a regex/keyword extractor if the API key is
missing, the call fails, or the response isn't valid/well-formed JSON."""
import json
import re
from datetime import datetime

import anthropic

import env_config

MODEL = "claude-sonnet-4-5"

REQUIRED_KEYS = {
    "candidate_name", "total_years_experience", "work_history", "education_level",
    "certifications", "equipment_experience", "physical_capability_mentioned",
    "shift_availability", "skills_keywords",
}

EXTRACTION_SYSTEM_PROMPT = """You are a data-extraction assistant. Your ONLY job is to read a candidate's raw CV
text and extract structured fields describing exactly what the text states. Do NOT
judge whether the candidate is qualified for any role, do NOT infer facts the text
does not state, and do NOT invent data. If a field is not mentioned, use null (or an
empty list / false, as appropriate for that field's type).

Return ONLY a single JSON object, with no markdown, no code fences, and no
commentary before or after it. The JSON object must have exactly these keys:

{
  "candidate_name": string or null,
  "total_years_experience": number or null,
  "work_history": [ {"employer": string, "title": string, "duration_years": number or null, "description": string} ],
  "education_level": string or null,
  "certifications": [string, ...],
  "equipment_experience": [string, ...],
  "physical_capability_mentioned": true or false,
  "shift_availability": string or null,
  "skills_keywords": [string, ...]
}

Rules:
- work_history must reflect only roles explicitly described with an employer and/or
  title, in the order they appear in the CV.
- skills_keywords should capture bare skill/tool mentions (e.g. from a "Skills"
  section) even when nothing elsewhere in the CV corroborates them -- this field is
  used downstream specifically to detect unsupported keyword claims, so list
  everything mentioned there without filtering.
- certifications should list only formally named certifications or licenses (e.g.
  "Forklift Operator Certification", "OSHA 10").
- physical_capability_mentioned should be true only if the CV explicitly states an
  ability to lift or carry a weight (specific or general)."""


def _get_api_key() -> str:
    return env_config.get_api_key()


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _call_claude_extract(client: "anthropic.Anthropic", raw_text: str) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": raw_text}],
    )
    return response.content[0].text


def _validate_extraction_schema(data) -> None:
    if not isinstance(data, dict):
        raise ValueError("extraction response is not a JSON object")
    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"extraction response missing keys: {missing}")
    if not isinstance(data["work_history"], list) or any(not isinstance(e, dict) for e in data["work_history"]):
        raise ValueError("work_history must be a list of objects")
    for key in ("certifications", "equipment_experience", "skills_keywords"):
        if not isinstance(data[key], list):
            raise ValueError(f"{key} must be a list")
    if not isinstance(data["physical_capability_mentioned"], bool):
        raise ValueError("physical_capability_mentioned must be a boolean")
    years = data["total_years_experience"]
    if years is not None and not isinstance(years, (int, float)):
        raise ValueError("total_years_experience must be a number or null")


def extract_candidate_fields(raw_text: str) -> tuple:
    """Returns (fields_dict, source) where source is 'llm' or 'fallback'."""
    try:
        api_key = _get_api_key()
        client = anthropic.Anthropic(api_key=api_key)
        response_text = _call_claude_extract(client, raw_text)
        cleaned = _strip_code_fences(response_text)
        data = json.loads(cleaned)
        _validate_extraction_schema(data)
        return data, "llm"
    except Exception as e:
        print(f"[llm_extract] falling back to regex extraction -- {type(e).__name__}: {e}", flush=True)
        return _fallback_extract(raw_text), "fallback"


# ---------------------------------------------------------------------------
# Regex/keyword fallback extractor
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^[A-Z][a-zA-Z.'-]+(\s+[A-Z][a-zA-Z.'-]+){1,2}$")
JOB_HEADER_RE = re.compile(
    r"^(?P<title>.+?),\s*(?P<employer>.+?)\s*\((?P<start>[A-Za-z]+\.?\s*\d{4})\s*[-–—]\s*"
    r"(?P<end>[Pp]resent|[A-Za-z]+\.?\s*\d{4})\)\s*$"
)
LIFT_MENTION_RE = re.compile(r"(lift\w*\s+(?:up to\s+)?\d{2,3}\s*(?:lbs|pounds))|(ability to lift)", re.I)

CERTIFICATION_TERMS = [
    "forklift operator certification", "forklift certification", "osha 10", "osha 30",
    "cpr certification", "servsafe",
]
EQUIPMENT_TERMS = ["forklift", "rf scanner", "wms", "sap wms", "pallet jack", "order picker", "voice pick"]
EDUCATION_TERMS = [
    "high school diploma", "ged", "associate degree", "bachelor degree", "some college", "high school",
]
SHIFT_TERMS = [
    "weekends", "weekend", "any shift", "flexible availability", "overnight",
    "night shift", "weekdays only", "day shift only",
]


def _year_of(date_str: str):
    m = re.search(r"\d{4}", date_str)
    return int(m.group(0)) if m else None


def _parse_work_history(lines: list) -> list:
    work_history = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = JOB_HEADER_RE.match(line)
        if m:
            start_year = _year_of(m.group("start"))
            end_raw = m.group("end")
            end_year = datetime.now().year if end_raw.lower() == "present" else _year_of(end_raw)
            duration_years = (end_year - start_year) if (start_year is not None and end_year is not None) else None

            j = i + 1
            desc_lines = []
            while j < len(lines) and lines[j].strip() and not JOB_HEADER_RE.match(lines[j].strip()):
                desc_lines.append(lines[j].strip())
                j += 1

            work_history.append({
                "employer": m.group("employer").strip(),
                "title": m.group("title").strip(),
                "duration_years": duration_years,
                "description": " ".join(desc_lines),
            })
            i = j
        else:
            i += 1
    return work_history


def _parse_skills_keywords(lines: list) -> list:
    for idx, line in enumerate(lines):
        if re.match(r"^\s*skills\s*:?\s*$", line, re.I):
            collected = []
            j = idx + 1
            while j < len(lines) and lines[j].strip():
                collected.append(lines[j].strip())
                j += 1
            blob = " ".join(collected)
            pieces = re.split(r"[,;•]+", blob)
            return [p.strip(" .-") for p in pieces if p.strip(" .-")]
    return []


def _fallback_extract(raw_text: str) -> dict:
    lines = raw_text.splitlines()
    lower_text = raw_text.lower()

    candidate_name = None
    for line in lines[:5]:
        stripped = line.strip()
        if stripped and NAME_RE.match(stripped):
            candidate_name = stripped
            break

    work_history = _parse_work_history(lines)

    year_mentions = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\+?\s*years?", raw_text, re.I)]
    duration_sum = sum(e["duration_years"] for e in work_history if e["duration_years"])
    year_candidates = year_mentions + ([duration_sum] if duration_sum else [])
    total_years_experience = max(year_candidates) if year_candidates else None

    education_level = next((t for t in EDUCATION_TERMS if t in lower_text), None)
    certifications = [t for t in CERTIFICATION_TERMS if t in lower_text]
    equipment_experience = [t for t in EQUIPMENT_TERMS if t in lower_text]
    physical_capability_mentioned = bool(LIFT_MENTION_RE.search(raw_text))
    shift_availability = next((t for t in SHIFT_TERMS if t in lower_text), None)
    skills_keywords = _parse_skills_keywords(lines)

    return {
        "candidate_name": candidate_name,
        "total_years_experience": total_years_experience,
        "work_history": work_history,
        "education_level": education_level,
        "certifications": certifications,
        "equipment_experience": equipment_experience,
        "physical_capability_mentioned": physical_capability_mentioned,
        "shift_availability": shift_availability,
        "skills_keywords": skills_keywords,
    }
