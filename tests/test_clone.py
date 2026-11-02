from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from code_grader.auth import GitCredentials
from code_grader.cloner import build_plan, clone_repositories, parse_limit, parse_submission
from code_grader.git_client import GitClient, GitError
from code_grader.github_activity import GitHubActivityClient, PushLookupError
from code_grader.models import CollectionError


def collection(urls, student_id="0012345"):
    return {"students": [{"student_id":student_id, "name":"홍길동", "submission_urls":urls}], "exceptions": []}


class PlanningTests(unittest.TestCase):
    def test_same_repo_is_grouped_but_other_repos_and_students_are_separate(self):
        data = collection(["https://github.com/student/homework/blob/main/main.c",
                           "https://github.com/Student/Homework/tree/main/src",
                           "https://github.com/student/other"])
        data["students"].append({"student_id":"0012346", "name":"김학생", "repository_urls":["https://github.com/student/homework"]})
        plans, exceptions = build_plan(data)
        self.assertEqual(len(plans), 3)
        self.assertEqual(len(plans[0].submissions), 2)
        self.assertEqual(exceptions, [])

    def test_gist_raw_and_unsupported_github_routes_are_exceptions(self):
        plans, exceptions = build_plan(collection([
            "https://gist.github.com/student/0123456789abcdef",
            "https://raw.githubusercontent.com/student/homework/main/main.c",
            "https://github.com/student/homework/edit/main/main.c",
            "https://github.com/student/homework/commit/abcdef12345",
        ]))
        self.assertEqual(plans, [])
        self.assertEqual([e["reason"] for e in exceptions], ["non_github_url", "non_github_url", "unsupported_submission_url", "unsupported_submission_url"])

    def test_duplicate_submission_and_encoded_korean_path(self):
        plans, exceptions = build_plan(collection([
            "https://github.com/student/homework/blob/main/%EA%B3%BC%EC%A0%9C/main.c",
            "https://github.com/student/homework/blob/main/%EA%B3%BC%EC%A0%9C/main.c",
        ]))
        self.assertEqual(plans[0].submissions[0].tail, "main/과제/main.c")
        self.assertEqual(len(plans[0].submissions), 1)
        self.assertEqual(exceptions, [])

    def test_path_traversal_is_rejected(self):
        for student_id in ("../outside", "/tmp/outside", "..", "clone-report.json"):
            plans, exceptions = build_plan(collection(["https://github.com/student/homework"], student_id))
            self.assertEqual(plans, [])
            self.assertEqual(exceptions[0]["reason"], "invalid_student_id")
        with self.assertRaises(ValueError):
            parse_submission("https://github.com/student/homework/blob/main/%2e%2e/secret")

    def test_timezone_is_required_for_limit(self):
        self.assertEqual(parse_limit("2026-01-02T09:00:00+09:00"), datetime(2026, 1, 2, tzinfo=timezone.utc))
        for value in ("2026-01-01", "invalid", 123):
            with self.assertRaises(CollectionError):
                parse_limit(value)


class GitCloneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Real Git operations use local bare repos via URL rewriting, so tests
        # exercise clone/worktree/checkout without network access or real tokens.
        self.gitconfig = self.root / "gitconfig"
        self.env = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL":str(self.gitconfig), "GIT_CONFIG_NOSYSTEM":"1", "GITHUB_TOKEN":"",
            "GIT_CONFIG_COUNT":"0", "GIT_TERMINAL_PROMPT":"0",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.work = self.root / "work"
        self.work.mkdir()
        self.git("init", "--initial-branch=main", str(self.work))
        self.git("config", "user.email", "test@example.invalid", repo=self.work)
        self.git("config", "user.name", "Test", repo=self.work)
        (self.work / "src").mkdir()
        (self.work / "src/main.c").write_text("old version\n")
        (self.work / "src/math.h").write_text("header\n")
        (self.work / "notes.txt").write_text("unsubmitted file\n")
        self.old = self.commit("initial", "2026-01-01T00:00:00+00:00")
        self.git("checkout", "-b", "feature/review", repo=self.work)
        (self.work / "src/main.c").write_text("feature version\n")
        self.feature = self.commit("feature", "2026-01-15T00:00:00+00:00")
        self.git("checkout", "main", repo=self.work)
        (self.work / "src/main.c").write_text("new version\n")
        self.new = self.commit("new main", "2026-02-01T00:00:00+00:00")
        remote = self.root / "remotes/student/homework.git"
        remote.parent.mkdir(parents=True)
        self.git("clone", "--bare", str(self.work), str(remote))
        self.git("config", "--file", str(self.gitconfig), "url." + (self.root / "remotes").as_uri() + "/.insteadOf", "https://github.com/")
        self.client = GitClient(GitCredentials("fake_test_token"))
        self.output = self.root / "output"

    def git(self, *args, repo=None, env=None):
        command = ["git"] + (["-C", str(repo)] if repo else []) + list(args)
        proc = subprocess.run(command, capture_output=True, text=True, env=env)
        if proc.returncode:
            self.fail(proc.stderr)
        return proc.stdout.strip()

    def commit(self, message, date):
        self.git("add", ".", repo=self.work)
        env = os.environ.copy()
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        self.git("commit", "-m", message, repo=self.work, env=env)
        return self.git("rev-parse", "HEAD", repo=self.work)

    def clone(self, urls, **kwargs):
        plans, exceptions = build_plan(collection(urls))
        return clone_repositories(plans, self.output, self.client, exceptions=exceptions, **kwargs)

    def test_multiple_files_are_cloned_once_and_whole_repo_is_available(self):
        with patch.object(self.client, "clone", wraps=self.client.clone) as clone:
            report = self.clone(["https://github.com/student/homework/blob/main/src/main.c",
                                 "https://github.com/student/homework/blob/main/src/math.h"])
        self.assertEqual(clone.call_count, 1)
        self.assertEqual(report["exceptions"], [])
        item = report["repositories"][0]
        self.assertEqual(item["status"], "cloned")
        checkout = Path(item["submissions"][0]["checkout"])
        self.assertEqual((checkout / "notes.txt").read_text(), "unsubmitted file\n")
        self.assertEqual(item["submissions"][0]["path"], "src/main.c")
        config = (checkout / ".git/config").read_text()
        self.assertNotIn("fake_test_token", config)
        self.assertNotIn("extraheader", config.lower())
        self.assertIn("https://github.com/student/homework.git", config)
        self.assertTrue((Path(item["directory"]) / "submission.json").is_file())

    def test_multiple_branches_and_slash_in_branch_name_have_separate_worktrees(self):
        report = self.clone(["https://github.com/student/homework/blob/main/src/main.c",
                             "https://github.com/student/homework/blob/feature/review/src/main.c"])
        self.assertEqual(report["exceptions"], [])
        main, feature = report["repositories"][0]["submissions"]
        self.assertEqual(main["commit"], self.new)
        self.assertEqual(feature["commit"], self.feature)
        self.assertEqual(feature["ref"], "feature/review")
        self.assertNotEqual(main["checkout"], feature["checkout"])
        self.assertEqual((Path(feature["checkout"]) / "src/main.c").read_text(), "feature version\n")
        self.assertEqual(self.git("rev-parse", "HEAD", repo=Path(feature["checkout"])), self.feature)

    def test_root_url_uses_default_branch_and_directory_type_is_checked(self):
        report = self.clone(["https://github.com/student/homework", "https://github.com/student/homework/tree/main/src"])
        self.assertEqual(report["exceptions"], [])
        root, directory = report["repositories"][0]["submissions"]
        self.assertEqual(root["ref"], "main")
        self.assertEqual(root["commit"], self.new)
        self.assertEqual(directory["path"], "src")

    def test_limit_uses_committer_timestamp_and_includes_exact_cutoff(self):
        report = self.clone(["https://github.com/student/homework"], limit=parse_limit("2026-01-01T09:00:00+09:00"))
        item = report["repositories"][0]
        self.assertEqual(item["submissions"][0]["commit"], self.old)
        self.assertEqual((Path(item["submissions"][0]["checkout"]) / "src/main.c").read_text(), "old version\n")

    def test_no_commit_before_limit_is_an_exception_and_leaves_no_destination(self):
        report = self.clone(["https://github.com/student/homework"], limit=parse_limit("2025-01-01T00:00:00Z"))
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["exceptions"][0]["reason"], "submission_resolution_failed")
        self.assertFalse(Path(report["repositories"][0]["directory"]).exists())

    def test_push_basis_excludes_old_commits_that_were_pushed_after_deadline(self):
        limit = parse_limit("2026-03-01T00:00:00Z")
        activity = GitHubActivityClient(self.client.credentials)
        events = [dict(id=2, ref="refs/heads/main", after=self.new, pushed_at="2026-03-02T00:00:00Z", push_type="normal"),
                  dict(id=1, ref="refs/heads/main", after=self.old, pushed_at="2026-01-02T00:00:00Z", push_type="normal")]
        with patch.object(activity, "_page", return_value=(events, None)) as page:
            report = self.clone(["https://github.com/student/homework/blob/main/src/main.c",
                                 "https://github.com/student/homework/blob/main/src/math.h"],
                                limit=limit, limit_basis="push", activity_client=activity)
        self.assertEqual(report["exceptions"], [])
        self.assertEqual(page.call_count, 1)
        first = report["repositories"][0]["submissions"][0]
        self.assertEqual(first["commit"], self.old)
        self.assertEqual(first["pushed_at"], "2026-01-02T00:00:00+00:00")
        self.assertEqual(first["push_activity_id"], "1")
        self.assertEqual(self.client.resolve_commit(Path(first["checkout"]), "refs/remotes/origin/main", limit), self.new)
        self.assertEqual((Path(first["checkout"]) / "src/main.c").read_text(), "old version\n")

    def test_push_basis_uses_push_time_even_when_commit_clock_is_in_the_future(self):
        activity = GitHubActivityClient(self.client.credentials)
        events = [dict(id=1, ref="refs/heads/main", after=self.new, pushed_at="2026-01-02T00:00:00Z", push_type="normal")]
        with patch.object(activity, "_page", return_value=(events, None)):
            report = self.clone(["https://github.com/student/homework"], limit=parse_limit("2026-01-10T00:00:00Z"),
                                limit_basis="push", activity_client=activity)
        self.assertEqual(report["exceptions"], [])
        self.assertEqual(report["repositories"][0]["submissions"][0]["commit"], self.new)

    def test_push_lookup_failure_is_an_exception_without_commit_date_fallback(self):
        activity = GitHubActivityClient(self.client.credentials)
        with patch.object(activity, "last_push", side_effect=PushLookupError("push_history_unavailable", "missing")):
            report = self.clone(["https://github.com/student/homework"], limit=parse_limit("2026-03-01T00:00:00Z"),
                                limit_basis="push", activity_client=activity)
        self.assertEqual(report["exceptions"][0]["reason"], "push_history_unavailable")
        self.assertEqual(report["repositories"][0]["submissions"], [])
        self.assertFalse(Path(report["repositories"][0]["directory"]).exists())

    def test_push_mode_without_deadline_uses_head_without_calling_api(self):
        activity = GitHubActivityClient(self.client.credentials)
        with patch.object(activity, "last_push", side_effect=AssertionError("unexpected API call")):
            report = self.clone(["https://github.com/student/homework"], limit_basis="push", activity_client=activity)
        self.assertEqual(report["exceptions"], [])
        self.assertEqual(report["repositories"][0]["submissions"][0]["commit"], self.new)

    def test_force_pushed_deadline_commit_can_be_fetched_from_same_origin(self):
        self.git("checkout", "--orphan", "rewritten", repo=self.work)
        (self.work / "src/main.c").write_text("rewritten version\n")
        self.commit("rewritten history", "2026-03-01T00:00:00+00:00")
        remote = self.root / "remotes/student/homework.git"
        self.git("push", "--force", str(remote), "rewritten:main", repo=self.work)
        self.git("update-ref", "-d", "refs/heads/feature/review", repo=remote)
        self.git("config", "uploadpack.allowAnySHA1InWant", "true", repo=remote)
        activity = GitHubActivityClient(self.client.credentials)
        events = [dict(id=1, ref="refs/heads/main", after=self.new, pushed_at="2026-02-02T00:00:00Z", push_type="normal")]
        with patch.object(activity, "_page", return_value=(events, None)), patch.object(self.client, "run", wraps=self.client.run) as run:
            report = self.clone(["https://github.com/student/homework"], limit=parse_limit("2026-02-15T00:00:00Z"),
                                limit_basis="push", activity_client=activity)
        self.assertEqual(report["exceptions"], [])
        self.assertTrue(any(call.args[0] == "fetch" for call in run.call_args_list))
        item = report["repositories"][0]["submissions"][0]
        self.assertEqual(item["commit"], self.new)
        self.assertEqual((Path(item["checkout"]) / "src/main.c").read_text(), "new version\n")

    def test_missing_push_commit_is_an_exception_without_a_different_checkout(self):
        activity = GitHubActivityClient(self.client.credentials)
        events = [dict(id=1, ref="refs/heads/main", after=self.old, pushed_at="2026-01-02T00:00:00Z", push_type="normal")]
        with patch.object(activity, "_page", return_value=(events, None)), patch.object(self.client, "ensure_commit", side_effect=GitError("unavailable")):
            report = self.clone(["https://github.com/student/homework"], limit=parse_limit("2026-03-01T00:00:00Z"),
                                limit_basis="push", activity_client=activity)
        self.assertEqual(report["exceptions"][0]["reason"], "push_commit_unavailable")
        self.assertEqual(report["repositories"][0]["submissions"], [])

    def test_bad_submission_does_not_stop_other_submissions(self):
        report = self.clone(["https://github.com/student/homework/blob/main/src/missing.c",
                             "https://github.com/student/homework/tree/main/src/main.c",
                             "https://github.com/student/homework/blob/main/src/main.c"])
        self.assertEqual(report["repositories"][0]["status"], "partial")
        self.assertEqual(len(report["exceptions"]), 2)
        self.assertEqual(len(report["repositories"][0]["submissions"]), 1)

    def test_existing_destination_is_preserved(self):
        self.clone(["https://github.com/student/homework"])
        path = self.output / "0012345/student/homework/repository/local-review.txt"
        path.write_text("keep my review")
        with patch.object(self.client, "clone", wraps=self.client.clone) as clone:
            report = self.clone(["https://github.com/student/homework"])
        self.assertEqual(clone.call_count, 0)
        self.assertEqual(report["exceptions"][0]["reason"], "destination_exists")
        self.assertEqual(path.read_text(), "keep my review")

    def test_clone_failure_is_reported_and_other_repositories_continue(self):
        report = self.clone(["https://github.com/student/missing", "https://github.com/student/homework"])
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["summary"]["cloned"], 1)
        self.assertEqual(report["exceptions"][0]["reason"], "clone_failed")

    def test_dry_run_has_no_git_operations_or_output_directory(self):
        with patch.object(self.client, "run", side_effect=AssertionError("unexpected Git call")):
            report = self.clone(["https://github.com/student/homework"], dry_run=True)
        self.assertEqual(report["repositories"][0]["status"], "planned")
        self.assertFalse(self.output.exists())

    def test_cli_reads_env_file_clones_and_preserves_collection_exceptions(self):
        source = self.root / "students.json"
        data = collection(["https://github.com/student/homework/blob/main/src/main.c"])
        data["exceptions"] = [{"student_id":"0012346", "reason":"non_github_url"}]
        source.write_text(json.dumps(data))
        env_file = self.root / ".env"
        env_file.write_text("GITHUB_TOKEN=env_file_test_token\n")
        proc = subprocess.run([sys.executable, "-m", "code_grader", "clone", str(source), "-o", str(self.output),
                               "--env-file", str(env_file)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["summary"]["cloned"], 1)
        self.assertEqual(report["input_exceptions"], data["exceptions"])
        self.assertTrue((self.output / "clone-report.json").exists())
        self.assertNotIn("env_file_test_token", proc.stdout + proc.stderr)
        self.assertNotIn("env_file_test_token", (self.output / "clone-report.json").read_text())

    def test_cli_dry_run_saves_push_basis_plan_without_creating_clone_directory(self):
        source = self.root / "students.json"
        source.write_text(json.dumps(collection(["https://github.com/student/homework"])))
        target = self.root / "plan.json"
        proc = subprocess.run([sys.executable, "-m", "code_grader", "clone", str(source), "-o", str(self.output),
                               "--dry-run", "--report", str(target), "--limit", "2026-03-01T00:00:00Z",
                               "--limit-basis", "push"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(target.read_text())["repositories"][0]["status"], "planned")
        self.assertEqual(json.loads(target.read_text())["limit_basis"], "push")
        self.assertFalse(self.output.exists())

    def test_cli_report_cannot_overwrite_credentials(self):
        source = self.root / "students.json"
        source.write_text(json.dumps(collection(["https://github.com/student/homework"])))
        env_file = self.root / ".env"
        env_file.write_text("GITHUB_TOKEN=keep_this_token\n")
        proc = subprocess.run([sys.executable, "-m", "code_grader", "clone", str(source), "--dry-run",
                               "--env-file", str(env_file), "--report", str(env_file)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(env_file.read_text(), "GITHUB_TOKEN=keep_this_token\n")
        self.assertNotIn("keep_this_token", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
