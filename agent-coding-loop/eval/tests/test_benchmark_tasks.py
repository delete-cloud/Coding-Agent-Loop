import json
import unittest
from pathlib import Path


BENCHMARK_TASKS = Path(__file__).resolve().parents[1] / "ab" / "benchmark_tasks.jsonl"
OLD_NOOP = 'python3 -c "import json; print(\'ok\')"'
EXPECTED_COMMANDS = {
    "kb_code_004": "python3 -m unittest eval.tests.test_kb_server",
    "kb_code_008": "python3 -m unittest eval.tests.test_kb_server",
    "kb_mixed_003": "python3 -m unittest eval.tests.test_run_ab",
}
STEP_2_2_ASSERTIONS = {
    "kb_code_001": [
        "grep -Fq",
        "api_key requires base_url",
        "internal/config/config.go",
    ],
    "kb_code_004": [
        "grep -Fq",
        "chunk_size must be between 100 and 8192",
        "kb/server.py",
    ],
    "kb_code_005": [
        "grep -Fq",
        "listen port must be >= 1024",
        "internal/config/config.go",
    ],
    "kb_code_006": [
        "grep -Fq",
        "no changes to submit",
        "internal/loop/engine_eino.go",
    ],
    "kb_code_008": [
        "grep -Fq",
        "overlap must be less than half of chunk_size",
        "kb/server.py",
    ],
    "kb_mixed_001": [
        "grep -Fq",
        "db_path must end with .db extension",
        "internal/config/config.go",
        "find internal/config -name '*test.go'",
    ],
    "kb_mixed_002": [
        "grep -Fq",
        "[BLOCKED]",
        "internal/loop/engine_eino.go",
        "find internal/loop -name '*test.go'",
    ],
    "kb_code_009": [
        "grep -Fq",
        "X-Request-Id",
        "internal/http/server.go",
        "find internal/http -name '*test.go'",
    ],
}
STEP_2_2_UNCHANGED = {
    "kb_code_002": "go build ./...",
    "kb_code_003": "go test ./internal/loop/...",
    "kb_code_007": "go test ./internal/model/...",
    "kb_mixed_003": "python3 -m unittest eval.tests.test_run_ab",
}


def load_task_map():
    task_map = {}
    with BENCHMARK_TASKS.open("r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            task_map[row["task_id"]] = row
    return task_map


class BenchmarkTasksTests(unittest.TestCase):
    def test_python_benchmark_tasks_use_targeted_unittest_commands(self):
        tasks = load_task_map()

        for task_id, expected_cmd in EXPECTED_COMMANDS.items():
            with self.subTest(task_id=task_id):
                self.assertIn(task_id, tasks)
                self.assertNotEqual(OLD_NOOP, tasks[task_id]["test_cmd"])
                self.assertTrue(tasks[task_id]["test_cmd"].startswith(expected_cmd))

    def test_step_2_2_rule_assertions_are_present_for_narrowed_task_set(self):
        tasks = load_task_map()

        for task_id, required_parts in STEP_2_2_ASSERTIONS.items():
            with self.subTest(task_id=task_id):
                self.assertIn(task_id, tasks)
                cmd = tasks[task_id]["test_cmd"]
                for required in required_parts:
                    self.assertIn(required, cmd)

    def test_step_2_2_keeps_removed_candidates_unchanged(self):
        tasks = load_task_map()

        for task_id, expected_cmd in STEP_2_2_UNCHANGED.items():
            with self.subTest(task_id=task_id):
                self.assertIn(task_id, tasks)
                self.assertEqual(expected_cmd, tasks[task_id]["test_cmd"])


if __name__ == "__main__":
    unittest.main()
