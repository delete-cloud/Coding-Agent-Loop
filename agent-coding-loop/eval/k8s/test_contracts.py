"""Static contract tests for the Step 4 K8S pipeline files."""

from __future__ import annotations

import unittest
from pathlib import Path


_THIS_DIR = Path(__file__).resolve().parent
_README = _THIS_DIR / "README.md"
_JOB_TMPL = _THIS_DIR / "job.yaml.tmpl"
_PVC = _THIS_DIR / "pvc.yaml"


class TestJobTemplateContract(unittest.TestCase):
    def test_uses_current_runtime_env_names(self):
        text = _JOB_TMPL.read_text(encoding="utf-8")
        self.assertIn("OPENAI_BASE_URL", text)
        self.assertIn("OPENAI_API_KEY", text)
        self.assertIn("OPENAI_MODEL", text)
        self.assertIn("AGENT_LOOP_KB_URL", text)
        self.assertIn("AGENT_LOOP_DB_PATH", text)
        self.assertNotIn("MODEL_BASE_URL", text)
        self.assertNotIn("MODEL_API_KEY", text)
        self.assertNotIn("MODEL_NAME", text)
        self.assertNotIn("KB_BASE_URL", text)

    def test_job_name_and_db_path_are_matrix_safe(self):
        text = _JOB_TMPL.read_text(encoding="utf-8")
        self.assertIn("name: eval-{{.TaskSlug}}-{{.Experiment}}-t{{.Trial}}", text)
        self.assertIn('task-id: "{{.TaskID}}"', text)
        self.assertIn('experiment: "{{.Experiment}}"', text)
        self.assertIn('trial: "{{.Trial}}"', text)
        self.assertIn(
            "value: /state/{{.Experiment}}/trial-{{.Trial}}/{{.TaskID}}/state.db",
            text,
        )


class TestReadmeContract(unittest.TestCase):
    def test_documents_local_cluster_scope(self):
        text = _README.read_text(encoding="utf-8")
        self.assertIn("local cluster", text.lower())
        self.assertIn("host machine", text.lower())
        self.assertIn("hostPath", text)

    def test_documents_render_placeholders_and_partitioned_results(self):
        text = _README.read_text(encoding="utf-8")
        self.assertIn("{{.TaskSlug}}", text)
        self.assertIn("{{.Experiment}}", text)
        self.assertIn("{{.Trial}}", text)
        self.assertIn("<experiment>/", text)
        self.assertIn("trial-<n>/", text)
        self.assertIn("<task_id>/", text)
        self.assertIn("state.db", text)
        self.assertIn(
            "--results-dir ./results/<experiment>/trial-<n>/",
            text,
        )


class TestPVCContract(unittest.TestCase):
    def test_pvc_mentions_local_hostpath_scope(self):
        text = _PVC.read_text(encoding="utf-8")
        self.assertIn("hostPath:", text)
        self.assertIn("eval-results", text)


if __name__ == "__main__":
    unittest.main()
