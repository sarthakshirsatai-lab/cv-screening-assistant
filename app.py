"""Flask app for the CV screening dashboard."""
import glob
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import Flask, jsonify, render_template, request

import database
import llm_extract
import llm_summary
import scoring

BASE_DIR = os.path.dirname(__file__)
JD_PATH = os.path.join(BASE_DIR, "data", "jd.json")
CVS_DIR = os.path.join(BASE_DIR, "data", "cvs")
MAX_SCREEN_WORKERS = 8

app = Flask(__name__)


def _load_jd_requirements() -> list:
    with open(JD_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["requirements"]


def _process_cv(cv_path: str, jd_requirements: list) -> dict:
    """Runs the LLM extraction + scoring + LLM summary for one CV. Pure
    computation, no shared state -- safe to run concurrently across threads."""
    cv_filename = os.path.basename(cv_path)
    with open(cv_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    extracted_fields, extraction_source = llm_extract.extract_candidate_fields(raw_text)
    verdict = scoring.score_candidate(extracted_fields, raw_text, jd_requirements)
    candidate_name = extracted_fields.get("candidate_name") or os.path.splitext(cv_filename)[0]
    summary_text, summary_source = llm_summary.generate_summary(
        candidate_name, verdict["fit_band"], verdict["criterion_scores"]
    )

    return {
        "cv_filename": cv_filename,
        "raw_text": raw_text,
        "candidate_name": candidate_name,
        "extracted_fields": extracted_fields,
        "extraction_source": extraction_source,
        "verdict": verdict,
        "summary_text": summary_text,
        "summary_source": summary_source,
    }


@app.route("/")
def dashboard():
    conn = database.get_connection()
    try:
        last_run = database.get_latest_run_at(conn)
    finally:
        conn.close()
    return render_template("dashboard.html", last_run=last_run)


@app.route("/api/screen", methods=["POST"])
def screen():
    jd_requirements = _load_jd_requirements()
    cv_paths = sorted(glob.glob(os.path.join(CVS_DIR, "*.txt")))
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Each candidate makes up to 2 blocking LLM calls (extract + summarize).
    # Run candidates concurrently on worker threads -- they release the GIL
    # while waiting on network I/O -- then do all SQLite writes afterward on
    # the main thread so the connection is never shared across threads.
    worker_count = min(MAX_SCREEN_WORKERS, len(cv_paths)) or 1
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        processed = list(executor.map(lambda p: _process_cv(p, jd_requirements), cv_paths))

    conn = database.get_connection()
    try:
        counts = {"Strong Fit": 0, "Needs Review": 0, "Likely Not a Fit": 0}

        for item in processed:
            verdict = item["verdict"]
            candidate_id = database.upsert_candidate(
                conn,
                cv_filename=item["cv_filename"],
                candidate_name=item["candidate_name"],
                raw_cv_text=item["raw_text"],
                extracted_fields=item["extracted_fields"],
                extraction_source=item["extraction_source"],
                total_score=verdict["total_score"],
                max_score=verdict["max_score"],
                fit_band=verdict["fit_band"],
                llm_summary=item["summary_text"],
                summary_source=item["summary_source"],
                screened_at=run_at,
            )
            database.replace_criterion_scores(conn, candidate_id, verdict["criterion_scores"])
            counts[verdict["fit_band"]] += 1

        database.insert_screening_run(
            conn,
            run_at=run_at,
            total=len(cv_paths),
            strong_fit=counts["Strong Fit"],
            needs_review=counts["Needs Review"],
            not_fit=counts["Likely Not a Fit"],
        )
        conn.commit()

        return jsonify({
            "run_at": run_at,
            "total_candidates": len(cv_paths),
            "strong_fit_count": counts["Strong Fit"],
            "needs_review_count": counts["Needs Review"],
            "not_fit_count": counts["Likely Not a Fit"],
        })
    finally:
        conn.close()


@app.route("/api/results")
def results():
    conn = database.get_connection()
    try:
        candidates = database.get_all_candidates_with_scores(conn)
    finally:
        conn.close()
    return jsonify(candidates)


@app.route("/api/candidate/<int:candidate_id>/decision", methods=["POST"])
def candidate_decision(candidate_id):
    payload = request.get_json(silent=True) or {}
    decision = payload.get("decision")
    if decision not in ("Shortlist", "Pass"):
        return jsonify({"error": "decision must be 'Shortlist' or 'Pass'"}), 400

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = database.get_connection()
    try:
        updated = database.set_human_decision(conn, candidate_id, decision, timestamp)
    finally:
        conn.close()

    if not updated:
        return jsonify({"error": "candidate not found"}), 404
    return jsonify({"id": candidate_id, "human_decision": decision, "decision_timestamp": timestamp})


if __name__ == "__main__":
    database.init_db()
    app.run(debug=True)
