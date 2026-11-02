import unittest

from code_grader.urls import extract_urls


class UrlTests(unittest.TestCase):
    def test_multiple_file_links_share_one_repository(self):
        result = extract_urls([
            "https://github.com/student/homework/blob/main/main.c\n"
            "https://github.com/student/homework/blob/main/math.h\n"
            "https://github.com/student/other/tree/main/hw1"
        ])
        self.assertEqual(len(result.urls), 3)
        self.assertEqual(result.repositories, ["https://github.com/student/homework", "https://github.com/student/other"])
        self.assertEqual(result.rejected, [])

    def test_fullwidth_broken_scheme_and_numbered_prefixes(self):
        result = extract_urls([
            "1번ＨＴＴＰＳ ://github.com/student/homework/blob/main/과제.c\n"
            "2.https://github.com/student/homework/blob/main/main.c\n"
            "README-https://github.com/student/homework/blob/main/README.md"
        ])
        self.assertEqual(len(result.urls), 3)
        self.assertTrue(result.urls[0].endswith("/과제.c"))
        self.assertEqual(len(result.repositories), 1)
        self.assertEqual(result.rejected, [])

    def test_delimiters_html_entities_and_balanced_parentheses(self):
        result = extract_urls([
            '[코드](https://github.com/student/homework/blob/main/f(1).c).',
            '<a href="https://github.com/student/homework?tab=readme&amp;x=1">코드</a>',
        ])
        self.assertEqual(result.urls[0], "https://github.com/student/homework/blob/main/f(1).c")
        self.assertEqual(result.urls[1], "https://github.com/student/homework?tab=readme&x=1")

    def test_schemeless_raw_and_ssh(self):
        result = extract_urls([
            "github.com/student/homework\nwww.github.com/student/homework.git\n"
            "https://raw.githubusercontent.com/student/homework/main/hello.c\n"
            "git@github.com:student/homework.git"
        ])
        self.assertEqual(len(result.repositories), 1)
        self.assertEqual(len(result.urls), 2)
        self.assertEqual(result.rejected[0].reason, "non_github_url")

    def test_deduplicate_owner_case_but_preserve_filename_case(self):
        result = extract_urls([
            "https://github.com/Student/Homework/blob/main/main.c",
            "https://github.com/student/homework/blob/main/main.c",
            "https://github.com/student/homework/blob/main/Main.c",
        ])
        self.assertEqual(len(result.urls), 2)
        self.assertEqual(len(result.repositories), 1)

    def test_gist_and_gist_raw(self):
        result = extract_urls([
            "HTTPS://gist.github.com/student/0123456789abcdef#file-main-c",
            "https://gist.githubusercontent.com/student/0123456789abcdef/raw/abc/main.c",
        ])
        self.assertEqual(result.urls, [])
        self.assertEqual(result.repositories, [])
        self.assertEqual(len(result.rejected), 2)
        self.assertTrue(all(link.reason == "non_github_url" for link in result.rejected))

    def test_invalid_links_are_reported_instead_of_guessed(self):
        result = extract_urls([
            "https://github.com/student\nhttps://github.com/login/oauth\n"
            "https://notgithub.com/student/homework"
        ])
        self.assertEqual(result.urls, [])
        self.assertEqual([link.reason for link in result.rejected], ["invalid_github_url", "invalid_github_url", "non_github_url"])

    def test_plain_prose_after_link_does_not_become_another_link(self):
        result = extract_urls(["https://github.com/student/homework // 설명 2026001\n과제 제출했습니다."])
        self.assertEqual(result.urls, ["https://github.com/student/homework"])

    def test_all_other_domains_are_exceptions_without_rewriting_them(self):
        result = extract_urls([
            "http://gitlab.com/student/homework",
            "https://drive.google.com/file/d/123/view",
            "https://github.com.evil.example/student/homework",
        ])
        self.assertEqual(result.repositories, [])
        self.assertEqual(len(result.rejected), 3)
        self.assertEqual(result.rejected[0].value, "http://gitlab.com/student/homework")

    def test_adjacent_links_are_not_swallowed_into_one_path(self):
        result = extract_urls(["https://github.com/a/first,https://github.com/b/second"])
        self.assertEqual(result.repositories, ["https://github.com/a/first", "https://github.com/b/second"])
        self.assertEqual(result.rejected, [])


if __name__ == "__main__":
    unittest.main()
