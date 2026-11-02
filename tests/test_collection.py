from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from code_grader.collector import collect, collect_sheets
from code_grader.excel import Cell, Sheet, read_excel
from code_grader.models import CollectionError


def row(*values):
    return [Cell(str(value)) for value in values]


class CollectionTests(unittest.TestCase):
    def test_header_after_title_and_id_in_submission_text(self):
        sheet = Sheet("과제", [row("과제명: 연습"), row("연번", "성명", "아이디", "제출여부", "과제설명"),
            row("1", "홍길동", "0012345", "제출", "https://github.com/student/2026001/blob/main/main.c")])
        result = collect_sheets([sheet])
        self.assertEqual(result.students[0].student_id, "0012345")
        self.assertEqual(result.students[0].name, "홍길동")
        self.assertEqual(result.warnings, [])

    def test_unsubmitted_is_optional_and_missing_links_remain_visible(self):
        sheet = Sheet("과제", [row("학번", "이름", "제출여부", "과제설명"),
            row("100001", "홍길동", "미제출", ""), row("100002", "김학생", "제출", "파일로 제출합니다.")])
        result = collect_sheets([sheet])
        self.assertEqual(result.students, [])
        self.assertEqual(result.exceptions[0].student_id, "100002")
        self.assertEqual(result.exceptions[0].reason, "missing_repository_url")
        self.assertEqual(result.skipped_unsubmitted, 1)
        self.assertEqual(len(result.exceptions), 1)
        all_students = collect_sheets([sheet], include_unsubmitted=True)
        self.assertEqual(len(all_students.exceptions), 2)
        self.assertEqual(all_students.exceptions[0].reason, "not_submitted")

    def test_missing_id_is_not_inherited_from_previous_student(self):
        sheet = Sheet("과제", [row("학번", "이름", "과제설명"),
            row("100001", "홍길동", "https://github.com/a/first"),
            row("", "다른학생", "https://github.com/b/second")])
        result = collect_sheets([sheet])
        self.assertEqual(len(result.students), 1)
        self.assertEqual(result.students[0].repository_urls, ["https://github.com/a/first"])
        self.assertEqual(result.exceptions[0].row, 3)

    def test_multiple_sheets_merge_same_student_and_deduplicate_links(self):
        first = Sheet("1", [row("학번", "이름", "링크"), row("100001", "홍길동", "https://github.com/a/first")])
        second = Sheet("2", [row("학번", "이름", "링크"),
            row("100001", "홍길동", "https://github.com/A/FIRST\nhttps://github.com/a/second")])
        result = collect_sheets([first, second])
        self.assertEqual(len(result.students), 1)
        self.assertEqual(len(result.students[0].repository_urls), 2)
        self.assertEqual(len(result.students[0].submission_urls), 2)

    def test_conflicting_names_are_exceptions(self):
        sheet = Sheet("과제", [row("학번", "이름", "링크"),
            row("100001", "홍길동", "https://github.com/a/first"),
            row("100001", "김학생", "https://github.com/b/second")])
        result = collect_sheets([sheet])
        self.assertEqual(len(result.students), 1)
        self.assertEqual(result.exceptions[0].reason, "conflicting_name")
        self.assertEqual(result.exceptions[0].name, "김학생")

    def test_mixed_github_and_other_links_have_separate_results(self):
        sheet = Sheet("과제", [row("학번", "이름", "링크"), row("100001", "홍길동",
            "https://github.com/a/homework\nhttps://gist.github.com/a/0123456789abcdef\nhttps://example.com/file")])
        result = collect_sheets([sheet])
        self.assertEqual(result.students[0].submission_urls, ["https://github.com/a/homework"])
        self.assertEqual(len(result.exceptions), 2)
        self.assertTrue(all(e.student_id == "100001" and e.name == "홍길동" and e.reason == "non_github_url" for e in result.exceptions))

    def test_repeated_headers_and_unrelated_sheets(self):
        sheet = Sheet("과제", [row("학번", "이름", "링크"), row("학번", "이름", "링크"),
            row("100001", "홍길동", "https://github.com/a/first")])
        result = collect_sheets([Sheet("설명", [row("참고 자료")]), sheet])
        self.assertEqual(len(result.students), 1)
        self.assertEqual(len(result.warnings), 1)

    def test_ambiguous_or_missing_headers_raise_error(self):
        for sheet in [Sheet("과제", [row("학번", "아이디", "이름")]), Sheet("과제", [row("설명")])]:
            with self.subTest(sheet=sheet):
                with self.assertRaises(CollectionError):
                    collect_sheets([sheet])

    def test_known_submission_column_excludes_feedback_links(self):
        sheet = Sheet("과제", [row("학번", "이름", "과제설명", "피드백"),
            row("100001", "홍길동", "https://github.com/a/homework", "https://github.com/teacher/sample")])
        self.assertEqual(collect_sheets([sheet]).students[0].repository_urls, ["https://github.com/a/homework"])


class ExcelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_xlsx_numeric_id_hyperlink_formula_and_sheet_selection(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "제출"
        sheet.append(["학번", "이름", "제출 주소"])
        sheet.append([12345, "홍길동", "코드 보기"])
        sheet["A2"].number_format = "0000000"
        sheet["C2"].hyperlink = "https://github.com/student/homework/blob/main/한글.c"
        sheet.append([12346, "김학생", '=HYPERLINK("https://github.com/student/other","보기")'])
        book.create_sheet("설명").append(["안내문"])
        path = self.root / "제출 목록.xlsx"
        book.save(path)
        result = collect(path, sheet_name="제출")
        self.assertEqual([s.student_id for s in result.students], ["0012345", "12346"])
        self.assertEqual(result.students[0].repository_urls, ["https://github.com/student/homework"])
        self.assertEqual(result.students[1].repository_urls, ["https://github.com/student/other"])
        self.assertEqual(result.warnings, [])
        with self.assertRaisesRegex(CollectionError, "시트를 찾을 수"):
            collect(path, sheet_name="없음")

    def test_html_disguised_as_xls_with_anchor_and_line_breaks(self):
        path = self.root / "export.xls"
        path.write_text('''<script>window.location = "ignored";</script><html><meta charset="UTF-8">
<table><tr><td colspan="4">과제명: 연습</td></tr></table>
<table><tr><th>학번</th><th>성명</th><th>제출여부</th><th>과제설명</th></tr>
<tr><td>0012345</td><td>홍길동</td><td>제출</td><td>
<a href="https://github.com/student/homework/blob/main/main.c">코드</a><br/>
2번HTTPS ://github.com/student/homework/blob/main/math.h</td></tr></table></html>''', encoding="utf-8")
        result = collect(path)
        self.assertEqual(result.students[0].student_id, "0012345")
        self.assertEqual(len(result.students[0].submission_urls), 2)
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(collect(path, sheet_name="HTML").students), 1)

    def test_cp949_html_export(self):
        path = self.root / "legacy.xls"
        path.write_bytes('''<meta charset="euc-kr"><table><tr><td>아이디</td><td>성명</td><td>과제설명</td></tr>
<tr><td>100001</td><td>홍길동</td><td>https://github.com/a/homework</td></tr></table>'''.encode("cp949"))
        self.assertEqual(collect(path).students[0].name, "홍길동")

    def test_invalid_and_missing_files_have_readable_errors(self):
        invalid = self.root / "broken.xls"
        invalid.write_bytes(b"not an excel file")
        for path in [invalid, self.root / "missing.xlsx", self.root / "input.txt"]:
            with self.subTest(path=path):
                with self.assertRaises(CollectionError):
                    read_excel(path)


if __name__ == "__main__":
    unittest.main()
