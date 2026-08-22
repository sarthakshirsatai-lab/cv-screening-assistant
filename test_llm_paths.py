"""Tests for both LLM call paths (extraction, summary) and their fallbacks,
plus the anti-keyword-stuffing rule in the scoring engine.

Run with: python -m unittest test_llm_paths.py -v
No ANTHROPIC_API_KEY is required -- the network-touching functions are mocked.
"""
import json
import os
import unittest
from unittest.mock import patch

import llm_extract
import llm_summary
import scoring

BASE_DIR = os.path.dirname(__file__)


def _read_cv(filename: str) -> str:
    with open(os.path.join(BASE_DIR, "data", "cvs", filename), "r", encoding="utf-8") as f:
        return f.read()


SAMPLE_CV = "Jane Doe\n\nExperience\nWarehouse Associate, Acme Distribution (Jan 2022 - Present)\nOperated a forklift.\n\nEducation\nHigh School Diploma\n\nSkills\nForklift, WMS\n"


class TestExtractionLLMPath(unittest.TestCase):
    def test_llm_success(self):
        valid_json = json.dumps({
            "candidate_name": "Distinctive Test Name",
            "total_years_experience": 3,
            "work_history": [{"employer": "Acme", "title": "Warehouse Associate", "duration_years": 3, "description": "Operated a forklift."}],
            "education_level": "High School Diploma",
            "certifications": ["Forklift Operator Certification"],
            "equipment_experience": ["forklift"],
            "physical_capability_mentioned": True,
            "shift_availability": "weekends",
            "skills_keywords": ["forklift", "wms"],
        })
        with patch("llm_extract._get_api_key", return_value="fake-key"), \
             patch("llm_extract.anthropic.Anthropic"), \
             patch("llm_extract._call_claude_extract", return_value=valid_json):
            fields, source = llm_extract.extract_candidate_fields(SAMPLE_CV)
        self.assertEqual(source, "llm")
        self.assertEqual(fields["candidate_name"], "Distinctive Test Name")

    def test_fallback_on_missing_api_key(self):
        with patch("llm_extract._get_api_key", side_effect=ValueError("no key")):
            fields, source = llm_extract.extract_candidate_fields(SAMPLE_CV)
        self.assertEqual(source, "fallback")
        self.assertIn("forklift", fields["equipment_experience"])

    def test_fallback_on_malformed_json(self):
        with patch("llm_extract._get_api_key", return_value="fake-key"), \
             patch("llm_extract.anthropic.Anthropic"), \
             patch("llm_extract._call_claude_extract", return_value="Sure! Here's the data: {not valid json"):
            fields, source = llm_extract.extract_candidate_fields(SAMPLE_CV)
        self.assertEqual(source, "fallback")

    def test_fallback_on_schema_mismatch(self):
        missing_key_json = json.dumps({
            "candidate_name": "Test",
            "total_years_experience": 1,
            "education_level": "GED",
            "certifications": [],
            "equipment_experience": [],
            "physical_capability_mentioned": False,
            "shift_availability": None,
            "skills_keywords": [],
        })  # missing "work_history"
        with patch("llm_extract._get_api_key", return_value="fake-key"), \
             patch("llm_extract.anthropic.Anthropic"), \
             patch("llm_extract._call_claude_extract", return_value=missing_key_json):
            fields, source = llm_extract.extract_candidate_fields(SAMPLE_CV)
        self.assertEqual(source, "fallback")

    def test_fallback_on_exception(self):
        with patch("llm_extract._get_api_key", return_value="fake-key"), \
             patch("llm_extract.anthropic.Anthropic"), \
             patch("llm_extract._call_claude_extract", side_effect=RuntimeError("API error")):
            fields, source = llm_extract.extract_candidate_fields(SAMPLE_CV)
        self.assertEqual(source, "fallback")


class TestSummaryLLMPath(unittest.TestCase):
    CRITERION_SCORES = [
        {"criterion_key": "years_experience", "criterion_label": "2+ years experience", "result": "Met"},
        {"criterion_key": "forklift_cert", "criterion_label": "Forklift certification", "result": "Gap"},
    ]

    def test_llm_success(self):
        with patch("llm_summary._get_api_key", return_value="fake-key"), \
             patch("llm_summary.anthropic.Anthropic"), \
             patch("llm_summary._call_claude_summary", return_value="A canned one-line summary."):
            text, source = llm_summary.generate_summary("Jane Doe", "Needs Review", self.CRITERION_SCORES)
        self.assertEqual(source, "llm")
        self.assertEqual(text, "A canned one-line summary.")

    def test_fallback_on_exception(self):
        with patch("llm_summary._get_api_key", return_value="fake-key"), \
             patch("llm_summary.anthropic.Anthropic"), \
             patch("llm_summary._call_claude_summary", side_effect=RuntimeError("API error")):
            text, source = llm_summary.generate_summary("Jane Doe", "Needs Review", self.CRITERION_SCORES)
        self.assertEqual(source, "fallback")
        self.assertIn("Jane Doe", text)
        self.assertTrue(len(text) > 0)

    def test_fallback_on_empty_response(self):
        with patch("llm_summary._get_api_key", return_value="fake-key"), \
             patch("llm_summary.anthropic.Anthropic"), \
             patch("llm_summary._call_claude_summary", return_value=""):
            text, source = llm_summary.generate_summary("Jane Doe", "Needs Review", self.CRITERION_SCORES)
        self.assertEqual(source, "fallback")
        self.assertTrue(len(text) > 0)

    def test_fallback_missing_api_key(self):
        with patch("llm_summary._get_api_key", side_effect=ValueError("no key")):
            text, source = llm_summary.generate_summary("Jane Doe", "Strong Fit", self.CRITERION_SCORES)
        self.assertEqual(source, "fallback")
        self.assertTrue(len(text) > 0)


class TestAntiStuffingScoring(unittest.TestCase):
    def _jd_requirements(self):
        with open(os.path.join(BASE_DIR, "data", "jd.json"), "r", encoding="utf-8") as f:
            return json.load(f)["requirements"]

    def test_stuffed_synthetic_input_never_met(self):
        stuffed = {
            "candidate_name": "Stuffed Candidate",
            "total_years_experience": None,
            "work_history": [],
            "education_level": None,
            "certifications": [],
            "equipment_experience": ["forklift", "rf scanner", "wms"],
            "physical_capability_mentioned": False,
            "shift_availability": None,
            "skills_keywords": ["forklift", "rf scanner", "wms", "inventory management", "order picking", "cycle counting"],
        }
        result = scoring.score_candidate(stuffed, raw_text="", jd_requirements=self._jd_requirements())
        for c in result["criterion_scores"]:
            self.assertNotEqual(c["result"], "Met", f"{c['criterion_key']} should not be Met without corroboration")
        self.assertEqual(result["fit_band"], "Likely Not a Fit")

    def test_stuffed_fixture_cv19_is_not_a_fit(self):
        raw_text = _read_cv("cv19.txt")
        with patch("llm_extract._get_api_key", side_effect=ValueError("no key")):
            fields, source = llm_extract.extract_candidate_fields(raw_text)
        self.assertEqual(source, "fallback")
        result = scoring.score_candidate(fields, raw_text, self._jd_requirements())
        self.assertEqual(result["fit_band"], "Likely Not a Fit")

    def test_stuffed_fixture_cv20_is_not_a_fit(self):
        raw_text = _read_cv("cv20.txt")
        with patch("llm_extract._get_api_key", side_effect=ValueError("no key")):
            fields, source = llm_extract.extract_candidate_fields(raw_text)
        self.assertEqual(source, "fallback")
        result = scoring.score_candidate(fields, raw_text, self._jd_requirements())
        self.assertEqual(result["fit_band"], "Likely Not a Fit")


if __name__ == "__main__":
    unittest.main()
