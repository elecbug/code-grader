import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from code_grader.auth import GitCredentials, load_token
from code_grader.git_client import GitClient, GitError
from code_grader.models import CollectionError


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env_file = self.root / ".env"
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_default_env_file_quoted_token_and_comments(self):
        self.env_file.write_text('\ufeffexport GITHUB_TOKEN="example_token" # comment\n', encoding="utf-8")
        with patch("os.getcwd", return_value=str(self.root)):
            self.assertEqual(load_token(), "example_token")

    def test_environment_can_override_env_file(self):
        self.env_file.write_text("GITHUB_TOKEN=file_token\n")
        os.environ["GITHUB_TOKEN"] = "environment_token"
        self.assertEqual(load_token(self.env_file), "environment_token")

    def test_missing_default_file_allows_public_clone_but_explicit_path_is_checked(self):
        with patch("os.getcwd", return_value=str(self.root)):
            self.assertIsNone(load_token())
        with self.assertRaises(CollectionError):
            load_token(self.env_file)

    def test_env_values_are_not_interpolated(self):
        self.env_file.write_text("OTHER=secret\nGITHUB_TOKEN='${OTHER}'\n")
        self.assertEqual(load_token(self.env_file), "${OTHER}")

    def test_token_with_newline_is_rejected_without_leaking_it(self):
        self.env_file.write_text('GITHUB_TOKEN="secret\nsecond_line"\n')
        with self.assertRaises(CollectionError) as error:
            load_token(self.env_file)
        self.assertNotIn("secret", str(error.exception))

    def test_git_auth_is_scoped_and_inherited_config_is_preserved(self):
        os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.name", GIT_CONFIG_VALUE_0="Tester",
                          GIT_TRACE_CURL="1", GIT_CURL_VERBOSE="1")
        credentials = GitCredentials("example_token")
        env = credentials.environment()
        config = {env[f"GIT_CONFIG_KEY_{i}"]:env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(config["user.name"], "Tester")
        self.assertEqual(config["credential.helper"], "")
        encoded = base64.b64encode(b"x-access-token:example_token").decode()
        self.assertEqual(config["http.https://github.com/.extraheader"], "Authorization: Basic " + encoded)
        self.assertNotIn("GIT_TRACE_CURL", env)
        self.assertNotIn("GIT_CURL_VERBOSE", env)

    def test_git_diagnostics_redact_plain_and_encoded_tokens(self):
        token = "sensitive_example_token"
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        failed = subprocess.CompletedProcess([], 1, "", f"remote: {token} Authorization: Basic {encoded}")
        with patch("code_grader.git_client.subprocess.run", return_value=failed) as run:
            with self.assertRaises(GitError) as error:
                GitClient(GitCredentials(token)).clone("https://github.com/student/homework.git", self.root / "repo")
        self.assertNotIn(token, str(error.exception))
        self.assertNotIn(encoded, str(error.exception))
        self.assertNotIn(token, " ".join(run.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
