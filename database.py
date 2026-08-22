"""SQLite persistence layer for the CV screening tool."""
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "screening.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cv_filename             TEXT NOT NULL UNIQUE,
    candidate_name          TEXT,
    raw_cv_text             TEXT NOT NULL,
    extracted_fields_json   TEXT NOT NULL,
    extraction_source       TEXT NOT NULL CHECK(extraction_source IN ('llm','fallback')),
    total_score             INTEGER NOT NULL,
    max_score               INTEGER NOT NULL,
    fit_band                TEXT NOT NULL CHECK(fit_band IN ('Strong Fit','Needs Review','Likely Not a Fit')),
    llm_summary             TEXT NOT NULL,
    summary_source          TEXT NOT NULL CHECK(summary_source IN ('llm','fallback')),
    human_decision          TEXT CHECK(human_decision IN ('Shortlist','Pass')),
    decision_timestamp      TEXT,
    screened_at             TEXT NOT NULL,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS criterion_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id        INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    criterion_key       TEXT NOT NULL,
    criterion_label     TEXT NOT NULL,
    result              TEXT NOT NULL CHECK(result IN ('Met','Partial','Gap')),
    points_awarded      INTEGER NOT NULL,
    points_possible     INTEGER NOT NULL,
    rationale           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screening_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at              TEXT NOT NULL,
    total_candidates    INTEGER NOT NULL,
    strong_fit_count    INTEGER NOT NULL,
    needs_review_count  INTEGER NOT NULL,
    not_fit_count       INTEGER NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def upsert_candidate(conn, *, cv_filename, candidate_name, raw_cv_text, extracted_fields,
                      extraction_source, total_score, max_score, fit_band, llm_summary,
                      summary_source, screened_at) -> int:
    extracted_json = json.dumps(extracted_fields)
    cur = conn.execute(
        """
        INSERT INTO candidates (
            cv_filename, candidate_name, raw_cv_text, extracted_fields_json,
            extraction_source, total_score, max_score, fit_band,
            llm_summary, summary_source, screened_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cv_filename) DO UPDATE SET
            candidate_name = excluded.candidate_name,
            raw_cv_text = excluded.raw_cv_text,
            extracted_fields_json = excluded.extracted_fields_json,
            extraction_source = excluded.extraction_source,
            total_score = excluded.total_score,
            max_score = excluded.max_score,
            fit_band = excluded.fit_band,
            llm_summary = excluded.llm_summary,
            summary_source = excluded.summary_source,
            screened_at = excluded.screened_at
        """,
        (cv_filename, candidate_name, raw_cv_text, extracted_json,
         extraction_source, total_score, max_score, fit_band,
         llm_summary, summary_source, screened_at),
    )
    if cur.lastrowid:
        row = conn.execute("SELECT id FROM candidates WHERE cv_filename = ?", (cv_filename,)).fetchone()
        return row["id"]
    row = conn.execute("SELECT id FROM candidates WHERE cv_filename = ?", (cv_filename,)).fetchone()
    return row["id"]


def replace_criterion_scores(conn, candidate_id: int, scores: list) -> None:
    conn.execute("DELETE FROM criterion_scores WHERE candidate_id = ?", (candidate_id,))
    conn.executemany(
        """
        INSERT INTO criterion_scores (
            candidate_id, criterion_key, criterion_label, result,
            points_awarded, points_possible, rationale
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (candidate_id, s["criterion_key"], s["criterion_label"], s["result"],
             s["points_awarded"], s["points_possible"], s["rationale"])
            for s in scores
        ],
    )


def insert_screening_run(conn, *, run_at, total, strong_fit, needs_review, not_fit) -> None:
    conn.execute(
        """
        INSERT INTO screening_runs (run_at, total_candidates, strong_fit_count, needs_review_count, not_fit_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_at, total, strong_fit, needs_review, not_fit),
    )


def get_latest_run_at(conn):
    row = conn.execute("SELECT run_at FROM screening_runs ORDER BY id DESC LIMIT 1").fetchone()
    return row["run_at"] if row else None


def get_all_candidates_with_scores(conn) -> list:
    candidates = conn.execute(
        "SELECT * FROM candidates ORDER BY total_score DESC, candidate_name ASC"
    ).fetchall()
    results = []
    for c in candidates:
        scores = conn.execute(
            "SELECT criterion_key, criterion_label, result, points_awarded, points_possible, rationale "
            "FROM criterion_scores WHERE candidate_id = ? ORDER BY id ASC",
            (c["id"],),
        ).fetchall()
        results.append({
            "id": c["id"],
            "cv_filename": c["cv_filename"],
            "candidate_name": c["candidate_name"],
            "extracted_fields": json.loads(c["extracted_fields_json"]),
            "extraction_source": c["extraction_source"],
            "total_score": c["total_score"],
            "max_score": c["max_score"],
            "fit_band": c["fit_band"],
            "llm_summary": c["llm_summary"],
            "summary_source": c["summary_source"],
            "human_decision": c["human_decision"],
            "decision_timestamp": c["decision_timestamp"],
            "screened_at": c["screened_at"],
            "criterion_scores": [dict(s) for s in scores],
        })
    return results


def set_human_decision(conn, candidate_id: int, decision: str, timestamp: str) -> bool:
    cur = conn.execute(
        "UPDATE candidates SET human_decision = ?, decision_timestamp = ? WHERE id = ?",
        (decision, timestamp, candidate_id),
    )
    conn.commit()
    return cur.rowcount > 0
