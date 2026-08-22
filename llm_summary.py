"""LLM call #2: turn the already-computed rule-based verdict into a one-line,
~20-word summary. The LLM never re-scores or second-guesses the verdict --
it only phrases it. Falls back to a template sentence if the call fails."""
import anthropic

import env_config

MODEL = "claude-sonnet-4-5"

SUMMARY_SYSTEM_PROMPT = """You write a single, concise, human-readable one-line summary (about 20 words,
never more than 35) of a candidate's already-finalized, rule-based fit
assessment, for a recruiter to scan at a glance. The fit band and per-criterion
results given to you are final and were computed by deterministic code -- you do
not judge, re-score, or second-guess them. Simply summarize them in plain,
neutral, factual language. Do not use the words "score" or "points", and do not
output any numbers. Return ONLY the one-line summary text: no quotes, no
markdown, no preamble, no explanation."""


def _get_api_key() -> str:
    return env_config.get_api_key()


def _build_summary_user_message(candidate_name: str, fit_band: str, criterion_scores: list) -> str:
    met = [c["criterion_label"] for c in criterion_scores if c["result"] == "Met"]
    partial = [c["criterion_label"] for c in criterion_scores if c["result"] == "Partial"]
    gap = [c["criterion_label"] for c in criterion_scores if c["result"] == "Gap"]
    return (
        f"Candidate: {candidate_name or 'Unknown'}\n"
        f"Fit band: {fit_band}\n"
        f"Criteria met: {', '.join(met) or 'none'}\n"
        f"Criteria partial: {', '.join(partial) or 'none'}\n"
        f"Criteria gaps: {', '.join(gap) or 'none'}\n\n"
        f"Write the one-line summary now."
    )


def _call_claude_summary(client: "anthropic.Anthropic", candidate_name: str, fit_band: str, criterion_scores: list) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=100,
        system=SUMMARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_summary_user_message(candidate_name, fit_band, criterion_scores)}],
    )
    return response.content[0].text.strip()


def generate_summary(candidate_name: str, fit_band: str, criterion_scores: list) -> tuple:
    """Returns (summary_text, source) where source is 'llm' or 'fallback'."""
    try:
        api_key = _get_api_key()
        client = anthropic.Anthropic(api_key=api_key)
        text = _call_claude_summary(client, candidate_name, fit_band, criterion_scores)
        if not text:
            raise ValueError("empty summary response")
        return text, "llm"
    except Exception as e:
        print(f"[llm_summary] falling back to template summary -- {type(e).__name__}: {e}", flush=True)
        return _fallback_summary(candidate_name, fit_band, criterion_scores), "fallback"


def _fallback_summary(candidate_name: str, fit_band: str, criterion_scores: list) -> str:
    name = candidate_name or "This candidate"
    met = [c["criterion_label"] for c in criterion_scores if c["result"] == "Met"]
    gaps = [c["criterion_label"] for c in criterion_scores if c["result"] == "Gap"]
    total = len(criterion_scores)

    if fit_band == "Strong Fit":
        highlight = ", ".join(met[:2]) if met else "the core role requirements"
        return f"{name} meets {len(met)} of {total} requirements, including {highlight} -- a strong match."
    if fit_band == "Needs Review":
        gap_text = ", ".join(gaps[:2]) if gaps else "a few areas"
        return f"{name} meets {len(met)} of {total} requirements but has gaps in {gap_text}, warranting manual review."
    gap_text = ", ".join(gaps[:3]) if gaps else "several core requirements"
    return f"{name} meets only {len(met)} of {total} requirements, with gaps in {gap_text}."
