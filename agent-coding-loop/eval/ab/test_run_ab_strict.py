import unittest

from run_ab import evaluate_strict_reasons


class StrictEvalTests(unittest.TestCase):
    def test_fallback_approve_forbidden(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="completed",
            checks={"requires_kb": False, "found_citation_count": 0},
            summary_text="Fallback reviewer approved: no failures detected in command output.",
            corpus_text="",
            trace={"meta_present": True, "reviewer_decision": "approve", "reviewer_used_fallback": True},
        )
        self.assertIn("fallback_approve_forbidden", reasons)

    def test_fallback_request_changes_is_allowed(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="completed",
            checks={"requires_kb": False, "found_citation_count": 0},
            summary_text="review requires changes",
            corpus_text="",
            trace={"meta_present": True, "reviewer_decision": "request_changes", "reviewer_used_fallback": True},
        )
        self.assertNotIn("fallback_approve_forbidden", reasons)

    def test_missing_citation_for_kb_task(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="completed",
            checks={"requires_kb": True, "found_citation_count": 0},
            summary_text="completed",
            corpus_text="",
            trace={"meta_present": True, "citations": []},
        )
        self.assertIn("missing_citation", reasons)
        self.assertIn("no_real_kb_search", reasons)

    def test_no_real_kb_search_for_kb_task(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="failed",
            checks={"requires_kb": True, "found_citation_count": 1},
            summary_text="failed",
            corpus_text="",
            trace={"meta_present": True, "citations": ["eval/ab/kb/rag_pipeline.md"], "kb_search_calls": 0},
        )
        self.assertIn("no_real_kb_search", reasons)

    def test_no_reason_when_not_strict(self):
        reasons = evaluate_strict_reasons(
            strict_mode=False,
            status="completed",
            checks={"requires_kb": True, "found_citation_count": 0},
            summary_text="Fallback reviewer approved",
            corpus_text="",
            trace={"meta_present": True, "fallback_used": True},
        )
        self.assertEqual([], reasons)

    def test_missing_structured_meta_for_completed_run(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="completed",
            checks={"requires_kb": False, "found_citation_count": 0},
            summary_text="completed",
            corpus_text="",
            trace={"meta_present": False},
        )
        self.assertIn("missing_structured_meta", reasons)

    def test_no_real_kb_search_when_backfill_only(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="completed",
            checks={"requires_kb": True, "found_citation_count": 1},
            summary_text="completed",
            corpus_text="",
            trace={"meta_present": True, "citations": ["eval/ab/kb/config_validation.md"], "kb_search_calls": 0},
        )
        self.assertIn("no_real_kb_search", reasons)
        self.assertNotIn("missing_citation", reasons)

    def test_no_real_kb_search_absent_when_tool_called(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="completed",
            checks={"requires_kb": True, "found_citation_count": 1},
            summary_text="completed",
            corpus_text="",
            trace={"meta_present": True, "citations": ["eval/ab/kb/config_validation.md"], "kb_search_calls": 2},
        )
        self.assertNotIn("no_real_kb_search", reasons)
        self.assertNotIn("missing_citation", reasons)

    def test_no_real_kb_search_not_applied_to_repo_only(self):
        reasons = evaluate_strict_reasons(
            strict_mode=True,
            status="completed",
            checks={"requires_kb": False, "found_citation_count": 0},
            summary_text="completed",
            corpus_text="",
            trace={"meta_present": True, "kb_search_calls": 0},
        )
        self.assertNotIn("no_real_kb_search", reasons)


if __name__ == "__main__":
    unittest.main()
