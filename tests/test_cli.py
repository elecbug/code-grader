import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "과제 제출.xls"
        self.input.write_text('''<table><tr><td>학번</td><td>이름</td><td>과제설명</td></tr>
<tr><td>0012345</td><td>홍길동</td><td>https://github.com/student/homework</td></tr>
<tr><td>0012346</td><td>김학생</td><td>https://gist.github.com/student/0123456789abcdef</td></tr></table>''', encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "code_grader", *map(str, args)], capture_output=True, text=True)

    def test_stdout_json_and_separate_diagnostics(self):
        proc = self.run_cli("collect", self.input)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["students"][0]["student_id"], "0012345")
        self.assertEqual(result["exceptions"][0]["reason"], "non_github_url")
        self.assertIn("예외 1건", proc.stderr)

    def test_csv_bom_and_automatic_exception_sidecar(self):
        output = self.root / "결과" / "students.csv"
        proc = self.run_cli("collect", self.input, "-o", output)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        with output.open(encoding="utf-8-sig", newline="") as source:
            students = list(csv.DictReader(source))
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]["student_id"], "0012345")
        exceptions = json.loads(output.with_name("students.exceptions.json").read_text())
        self.assertEqual(exceptions[0]["name"], "김학생")

    def test_cannot_overwrite_input_or_use_colliding_output_paths(self):
        original = self.input.read_bytes()
        for args in [
            ("-o", self.input),
            ("--exceptions-output", self.input),
            ("-o", self.root / "students.json", "--exceptions-output", self.root / "students.json"),
        ]:
            with self.subTest(args=args):
                proc = self.run_cli("collect", self.input, *args)
                self.assertEqual(proc.returncode, 1)
                self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(self.input.read_bytes(), original)

    def test_missing_input_fails_without_partial_output(self):
        output = self.root / "students.json"
        proc = self.run_cli("collect", self.root / "missing.xls", "-o", output)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
