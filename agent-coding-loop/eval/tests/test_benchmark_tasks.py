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
                self.assertEqual(expected_cmd, tasks[task_id]["test_cmd"])


if __name__ == "__main__":
    unittest.main()
