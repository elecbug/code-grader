from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from code_grader.merger import merge_all, merge_directory
from code_grader.models import CollectionError


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "과제 폴더"
        self.root.mkdir()

    def write(self, name, text, encoding="utf-8"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
        return path

    def test_legacy_format_sorted_relative_paths_and_default_extensions(self):
        self.write("src/과제.c", "int main(void) {}\n")
        self.write("README.md", "과제 설명\n")
        self.write("include/math.h", "int add(int, int);")
        self.write("notes.txt", "not included")
        result = merge_directory(self.root)
        self.assertEqual(result.files, 3)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.output.read_text(),
            "# README.md\n\n과제 설명\n\n\n"
            "# include/math.h\n\nint add(int, int);\n\n"
            "# src/과제.c\n\nint main(void) {}\n\n\n")

    def test_repeated_merge_replaces_output_without_duplicating_it(self):
        self.write("main.c", "source")
        first = merge_directory(self.root).output.read_text()
        result = merge_directory(self.root)
        self.assertEqual(result.output.read_text(), first)
        self.assertEqual(result.files, 1)

    def test_custom_markdown_output_does_not_include_itself(self):
        self.write("main.c", "source")
        output = self.write("combined.md", "stale output")
        first = merge_directory(self.root, output=output).output.read_text()
        second = merge_directory(self.root, output=output)
        self.assertNotIn("stale output", first)
        self.assertNotIn("# combined.md", first)
        self.assertEqual(second.output.read_text(), first)

    def test_stdout_mode_creates_no_file(self):
        self.write("main.c", "source")
        stream = StringIO()
        result = merge_directory(self.root, stream=stream)
        self.assertIsNone(result.output)
        self.assertEqual(stream.getvalue(), "# main.c\n\nsource\n\n")
        self.assertFalse((self.root / "merged_output.txt").exists())

    def test_encoding_support_and_original_files_are_preserved(self):
        paths = [self.write("한글.c", "한글 주석\n", "cp949"),
                 self.write("wide.h", "유니코드 헤더\n", "utf-16"),
                 self.write("README.md", "BOM 문서\n", "utf-8-sig")]
        before = [path.read_bytes() for path in paths]
        result = merge_directory(self.root)
        text = result.output.read_text()
        self.assertEqual(result.files, 3)
        self.assertEqual(result.errors, [])
        self.assertIn("한글 주석", text)
        self.assertIn("유니코드 헤더", text)
        self.assertIn("BOM 문서", text)
        self.assertNotIn("\ufeff", text)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_git_dependencies_and_symlinks_are_skipped(self):
        self.write("main.c", "source")
        for name in (".git/internal.md", ".venv/library.c", "node_modules/library.c"):
            self.write(name, "excluded content")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("external content")
        (self.root / "linked.md").symlink_to(outside / "secret.md")
        (self.root / "linked-dir").symlink_to(outside, target_is_directory=True)
        result = merge_directory(self.root)
        self.assertEqual(result.files, 1)
        self.assertEqual(result.output.read_text(), "# main.c\n\nsource\n\n")

    def test_binary_or_undecodable_source_is_reported_and_other_files_continue(self):
        self.write("main.c", "source")
        (self.root / "binary.c").write_bytes(b"binary\x00data")
        (self.root / "bad.h").write_bytes(b"\xff\x81")
        result = merge_directory(self.root)
        self.assertEqual(result.files, 1)
        self.assertEqual(len(result.errors), 2)
        self.assertEqual(result.output.read_text(), "# main.c\n\nsource\n\n")

    def test_custom_extensions_support_other_languages(self):
        self.write("main.py", "print('hello')")
        self.write("Main.JAVA", "class Main {}")
        self.write("main.c", "excluded")
        result = merge_directory(self.root, extensions=["py", ".java"])
        self.assertEqual(result.files, 2)
        text = result.output.read_text()
        self.assertIn("# Main.JAVA", text)
        self.assertIn("# main.py", text)
        self.assertNotIn("excluded", text)

    def test_output_symlink_does_not_overwrite_its_target(self):
        self.write("main.c", "source")
        outside = Path(self.tmp.name) / "important.txt"
        outside.write_text("keep this")
        output = self.root / "merged_output.txt"
        output.symlink_to(outside)
        result = merge_directory(self.root)
        self.assertFalse(result.output.is_symlink())
        self.assertEqual(outside.read_text(), "keep this")
        self.assertIn("# main.c", output.read_text())

    def test_empty_directory_produces_an_empty_file(self):
        result = merge_directory(self.root)
        self.assertEqual(result.files, 0)
        self.assertEqual(result.output.read_bytes(), b"")

    def test_missing_directory_or_invalid_extension_has_readable_error(self):
        with self.assertRaises(CollectionError):
            merge_directory(self.root / "missing")
        with self.assertRaises(CollectionError):
            merge_directory(self.root, extensions=["../c"])

    def test_all_writes_each_source_directory_and_ancestor_with_relative_headers(self):
        self.write("first/src/main.c", "first code")
        self.write("second/README.md", "second code")
        (self.root / "empty").mkdir()
        results, errors = merge_all(self.root)
        self.assertEqual(errors, [])
        self.assertEqual({r.directory.relative_to(self.root).as_posix() for r in results}, {".", "first", "first/src", "second"})
        root_output = (self.root / "merged_output.txt").read_text()
        self.assertIn("# first/src/main.c", root_output)
        self.assertIn("# second/README.md", root_output)
        self.assertIn("# src/main.c", (self.root / "first/merged_output.txt").read_text())
        self.assertIn("# main.c", (self.root / "first/src/merged_output.txt").read_text())
        self.assertFalse((self.root / "empty/merged_output.txt").exists())
        merge_all(self.root)
        self.assertEqual((self.root / "merged_output.txt").read_text(), root_output)

    def test_generated_outputs_are_excluded_even_when_txt_is_selected(self):
        self.write("src/notes.txt", "notes")
        self.write("src/merged_output.txt", "stale nested output")
        results, errors = merge_all(self.root, extensions=["txt"])
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertNotIn("stale nested output", (self.root / "merged_output.txt").read_text())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source folder"
        self.root.mkdir()
        (self.root / "main.c").write_text("source")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "code_grader", "merge", str(self.root), *map(str, args)], capture_output=True, text=True)

    def test_default_output_and_stdout_option(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        expected = (self.root / "merged_output.txt").read_text()
        result = self.run_cli("--stdout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertIn("1개 파일", result.stderr)

    def test_custom_output_and_all_modes_are_mutually_exclusive(self):
        output = Path(self.tmp.name) / "result.txt"
        result = self.run_cli("-o", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# main.c", output.read_text())
        self.assertEqual(self.run_cli("--all", "-o", output).returncode, 2)
        self.assertEqual(self.run_cli("--stdout", "-o", output).returncode, 2)

    def test_binary_source_returns_nonzero_with_a_readable_diagnostic(self):
        (self.root / "binary.h").write_bytes(b"\x00binary")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("binary.h", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("# main.c", (self.root / "merged_output.txt").read_text())

    def test_all_mode_and_custom_extensions(self):
        (self.root / "child").mkdir()
        (self.root / "child/main.py").write_text("print('hello')")
        result = self.run_cli("--all", "--extensions", "py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# child/main.py", (self.root / "merged_output.txt").read_text())
        self.assertIn("# main.py", (self.root / "child/merged_output.txt").read_text())
        self.assertNotIn("# main.c", (self.root / "merged_output.txt").read_text())


if __name__ == "__main__":
    unittest.main()
