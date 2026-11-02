import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, is_zipfile

from code_grader.merger import archive_directory
from code_grader.models import CollectionError


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "전체 과제"
        self.root.mkdir()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_preserves_all_student_folders_original_files_and_metadata(self):
        files = {
            "0012345/student/homework/repository/과제.c":"한글 주석".encode("cp949"),
            "0012345/student/homework/repository/image.png":b"\x89PNG\r\n\x00\xff",
            "0012345/student/homework/submission.json":b'{"student_id":"0012345"}',
            "0012346/another/project/repository/main.py":b"print('hello')\n",
            "0012346/another/project/repository/report.pdf":b"%PDF-1.7\x00binary",
            "0012346/merged_output.txt":b"existing merged text",
            "clone-report.json":b'{"repositories":[]}',
        }
        for name, data in files.items():
            self.write(name, data)
        result = archive_directory(self.root)
        self.assertEqual(result.output, self.root.parent / "전체 과제.zip")
        self.assertEqual(result.files, len(files))
        with ZipFile(result.output) as archive:
            self.assertIsNone(archive.testzip())
            archived = {name:archive.read(name) for name in archive.namelist() if not name.endswith("/")}
            self.assertEqual(archived, files)

    def test_empty_student_directories_are_preserved(self):
        (self.root / "0012345").mkdir()
        (self.root / "0012346/empty-project").mkdir(parents=True)
        result = archive_directory(self.root)
        self.assertEqual(result.files, 0)
        with ZipFile(result.output) as archive:
            self.assertEqual(set(archive.namelist()), {"0012345/", "0012346/", "0012346/empty-project/"})

    def test_git_internals_worktree_marker_and_symlinks_are_skipped(self):
        self.write("0012345/repository/main.c", b"source")
        self.write("0012345/repository/.git/config", b"git metadata")
        self.write("0012345/checkouts/commit/.git", b"gitdir: /outside/.git/worktrees/commit")
        self.write("0012345/checkouts/commit/main.c", b"other version")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("outside content")
        (self.root / "file-link").symlink_to(outside / "file.txt")
        (self.root / "directory-link").symlink_to(outside, target_is_directory=True)
        result = archive_directory(self.root)
        with ZipFile(result.output) as archive:
            self.assertEqual({name for name in archive.namelist() if not name.endswith("/")},
                {"0012345/repository/main.c", "0012345/checkouts/commit/main.c"})
            self.assertFalse(any(".git" in Path(name).parts for name in archive.namelist()))

    def test_zip_includes_dependency_folders_as_part_of_complete_tree(self):
        self.write("0012345/project/node_modules/example/source.js", b"dependency")
        result = archive_directory(self.root)
        with ZipFile(result.output) as archive:
            self.assertIn("0012345/project/node_modules/example/source.js", archive.namelist())

    def test_nested_output_does_not_archive_itself_or_temporary_files_on_rerun(self):
        self.write("0012345/main.c", b"source")
        output = self.root / "all.zip"
        for _ in range(2):
            result = archive_directory(self.root, output=output)
            self.assertEqual(result.files, 1)
            with ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"0012345/", "0012345/main.c"})

    def test_explicit_extensions_filter_files_but_keep_student_directories(self):
        self.write("0012345/main.py", b"print('hello')")
        self.write("0012345/image.png", b"\x00image")
        self.write("0012346/main.c", b"source")
        result = archive_directory(self.root, extensions=["py"])
        with ZipFile(result.output) as archive:
            self.assertEqual(set(archive.namelist()), {"0012345/", "0012345/main.py", "0012346/"})

    def test_read_failure_preserves_existing_archive_and_removes_temporary_file(self):
        self.write("0012345/main.c", b"source")
        output = archive_directory(self.root).output
        before = output.read_bytes()
        with patch("code_grader.merger.ZipFile.write", side_effect=PermissionError("unreadable input")):
            with self.assertRaises(OSError):
                archive_directory(self.root)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(list(output.parent.glob(".archive-*.tmp")), [])

    def test_output_symlink_does_not_overwrite_its_target(self):
        self.write("0012345/main.c", b"source")
        original = Path(self.tmp.name) / "original.txt"
        original.write_text("preserve me")
        output = self.root / "all.zip"
        output.symlink_to(original)
        archive_directory(self.root, output=output)
        self.assertFalse(output.is_symlink())
        self.assertTrue(is_zipfile(output))
        self.assertEqual(original.read_text(), "preserve me")

    def test_old_file_timestamp_can_be_archived(self):
        source = self.write("0012345/main.c", b"old source")
        os.utime(source, (0, 0))
        result = archive_directory(self.root)
        with ZipFile(result.output) as archive:
            self.assertEqual(archive.read("0012345/main.c"), b"old source")

    def test_missing_directory_is_a_readable_error(self):
        with self.assertRaises(CollectionError):
            archive_directory(self.root / "missing")


class ArchiveCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "전체 학생"
        (self.root / "0012345").mkdir(parents=True)
        (self.root / "0012345/main.c").write_text("source")
        (self.root / "0012345/main.py").write_text("print('hello')")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "code_grader", "merge", str(self.root), *map(str, args)], capture_output=True, text=True)

    def test_zip_flag_and_default_output(self):
        result = self.run_cli("--zip")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("ZIP:", result.stderr)
        with ZipFile(self.root.parent / "전체 학생.zip") as archive:
            self.assertIn("0012345/main.py", archive.namelist())
            self.assertIn("0012345/main.c", archive.namelist())

    def test_custom_zip_output_is_detected_from_extension(self):
        output = Path(self.tmp.name) / "result" / "students.zip"
        result = self.run_cli("-o", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(is_zipfile(output))

    def test_explicit_zip_output_with_extensions(self):
        output = Path(self.tmp.name) / "students.zip"
        result = self.run_cli("--zip", "-o", output, "--extensions", ".py")
        self.assertEqual(result.returncode, 0, result.stderr)
        with ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {"0012345/", "0012345/main.py"})

    def test_zip_is_incompatible_with_stdout_and_per_folder_merge(self):
        self.assertEqual(self.run_cli("--zip", "--stdout").returncode, 2)
        self.assertEqual(self.run_cli("--zip", "--all").returncode, 2)


if __name__ == "__main__":
    unittest.main()
