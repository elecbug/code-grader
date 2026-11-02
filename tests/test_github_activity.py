from datetime import datetime, timezone
from io import BytesIO
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import patch

from code_grader.auth import GitCredentials
from code_grader.github_activity import GitHubActivityClient, PushLookupError


REPO = "https://github.com/student/homework"
REF = "refs/heads/main"
CUTOFF = datetime(2026, 3, 1, tzinfo=timezone.utc)


def activity(commit="a" * 40, pushed_at="2026-02-01T00:00:00Z", ref=REF, **fields):
    return dict(id=1, after=commit, timestamp=pushed_at, ref=ref, activity_type="push", **fields)


class Response(BytesIO):
    def __init__(self, data, links=""):
        super().__init__(json.dumps(data).encode())
        self.headers = {"Link": links}


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.client = GitHubActivityClient(GitCredentials("test_api_token"))

    def test_selects_last_push_at_or_before_limit_on_matching_branch(self):
        events = [activity("b" * 40, "2026-03-02T00:00:00Z"),
                  activity("c" * 40, "2026-03-01T00:00:00Z", "refs/heads/other"),
                  activity("a" * 40, "2026-03-01T00:00:00Z"),
                  activity("d" * 40, "2026-02-01T00:00:00Z")]
        with patch.object(self.client, "_page", return_value=(events, None)):
            result = self.client.last_push(REPO, REF, CUTOFF)
        self.assertEqual(result.commit, "a" * 40)
        self.assertEqual(result.pushed_at, "2026-03-01T00:00:00+00:00")

    def test_follows_cursor_pages_and_caches_result_for_multiple_files(self):
        next_url = "https://api.github.com/repos/student/homework/activity?after=cursor"
        pages = [([activity(pushed_at="2026-03-02T00:00:00Z")], next_url), ([activity()], None)]
        with patch.object(self.client, "_page", side_effect=pages) as page:
            first = self.client.last_push(REPO, REF, CUTOFF)
            second = self.client.last_push(REPO, REF, CUTOFF)
        self.assertEqual(first, second)
        self.assertEqual(page.call_count, 2)
        self.assertEqual(page.call_args.args[0], next_url)

    def test_unknown_history_does_not_select_a_newer_push_or_invent_a_commit(self):
        with patch.object(self.client, "_page", return_value=([activity(pushed_at="2026-03-02T00:00:00Z")], None)) as page:
            for _ in range(2):
                with self.assertRaises(PushLookupError) as error:
                    self.client.last_push(REPO, REF, CUTOFF)
                self.assertEqual(error.exception.reason, "push_history_unavailable")
        self.assertEqual(page.call_count, 1)

    def test_deleted_branch_is_not_silently_restored_from_an_earlier_push(self):
        pages = [activity("0" * 40), activity("a" * 40, "2026-01-01T00:00:00Z")]
        with patch.object(self.client, "_page", return_value=(pages, None)):
            with self.assertRaises(PushLookupError) as error:
                self.client.last_push(REPO, REF, CUTOFF)
        self.assertEqual(error.exception.reason, "ref_deleted_at_limit")

    def test_invalid_timestamp_or_commit_is_an_exception(self):
        for item in (activity(pushed_at="2026-01-01"), activity(commit="not-a-commit")):
            client = GitHubActivityClient(GitCredentials())
            with self.subTest(item=item), patch.object(client, "_page", return_value=([item], None)):
                with self.assertRaises(PushLookupError) as error:
                    client.last_push(REPO, REF, CUTOFF)
                self.assertEqual(error.exception.reason, "push_history_invalid")

    def test_current_and_legacy_api_response_field_names_are_supported(self):
        for item in [activity(), dict(id=1, after="a" * 40, ref=REF, pushed_at="2026-02-01T00:00:00Z", push_type="normal")]:
            client = GitHubActivityClient(GitCredentials())
            with self.subTest(item=item), patch.object(client, "_page", return_value=([item], None)):
                result = client.last_push(REPO, REF, CUTOFF)
                self.assertEqual(result.commit, "a" * 40)
                self.assertEqual(result.pushed_at, "2026-02-01T00:00:00+00:00")
                self.assertIn(result.push_type, {"push", "normal"})

    def test_branch_deletion_activity_with_no_after_sha_is_an_exception(self):
        item = activity(commit=None)
        item["activity_type"] = "branch_deletion"
        with patch.object(self.client, "_page", return_value=([item], None)):
            with self.assertRaises(PushLookupError) as error:
                self.client.last_push(REPO, REF, CUTOFF)
        self.assertEqual(error.exception.reason, "ref_deleted_at_limit")

    def test_commit_sha_alone_has_no_branch_push_history(self):
        with patch.object(self.client, "_page", side_effect=AssertionError("unexpected request")):
            with self.assertRaises(PushLookupError) as error:
                self.client.last_push(REPO, "a" * 40, CUTOFF)
        self.assertEqual(error.exception.reason, "push_ref_required")

    def test_api_uses_token_in_header_and_ref_query_without_time_filter(self):
        with patch.object(self.client.opener, "open", return_value=Response([activity()])) as opened:
            self.client.last_push(REPO, REF, CUTOFF)
        request = opened.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["ref"], [REF])
        self.assertEqual(query["direction"], ["desc"])
        self.assertNotIn("time_period", query)
        self.assertNotIn("before", query)  # before is a cursor, not a timestamp.
        self.assertEqual(request.get_header("Authorization"), "Bearer test_api_token")
        self.assertNotIn("test_api_token", request.full_url)

    def test_pagination_cannot_forward_credentials_to_another_host(self):
        response = Response([], '<https://example.com/activity>; rel="next"')
        with patch.object(self.client.opener, "open", return_value=response) as opened:
            with self.assertRaises(PushLookupError):
                self.client.last_push(REPO, REF, CUTOFF)
        self.assertEqual(opened.call_count, 1)

    def test_http_error_does_not_expose_credentials(self):
        failure = HTTPError("https://api.github.com/", 403, "test_api_token", {}, None)
        with patch.object(self.client.opener, "open", side_effect=failure):
            with self.assertRaises(PushLookupError) as error:
                self.client.last_push(REPO, REF, CUTOFF)
        self.assertEqual(error.exception.reason, "push_api_failed")
        self.assertNotIn("test_api_token", str(error.exception))

    def test_non_list_response_is_not_treated_as_empty_history(self):
        with patch.object(self.client.opener, "open", return_value=Response({"message":"error"})):
            with self.assertRaises(PushLookupError) as error:
                self.client.last_push(REPO, REF, CUTOFF)
        self.assertEqual(error.exception.reason, "push_history_invalid")


if __name__ == "__main__":
    unittest.main()
