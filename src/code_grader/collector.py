from pathlib import Path
import re

from .excel import Cell, Sheet, read_excel
from .models import Collection, CollectionError, Student, SubmissionException, Warning
from .urls import extract_urls, normalize_text, submission_key


def header_key(text: str) -> str:
    return re.sub(r"[\s_\-/()]", "", normalize_text(text)).lower()


ID_HEADERS = {"학번", "아이디", "학번아이디", "학생번호", "studentid", "id"}
NAME_HEADERS = {"성명", "이름", "학생명", "학생이름", "name", "studentname"}
STATUS_HEADERS = {"제출여부", "제출상태", "submissionstatus", "submitted"}
URL_HEADERS = {
    "과제설명", "과제내용", "제출내용", "제출물", "제출주소", "제출링크",
    "저장소주소", "레포지토리주소", "레포지토리", "저장소", "깃허브", "깃허브주소",
    "repository", "repositoryurl", "repositoryurls", "github", "githuburl",
    "url", "urls", "링크", "주소",
}
UNSUBMITTED = {"미제출", "제출안함", "미완료", "n", "no", "false", "0"}


def _find_header(sheet: Sheet) -> tuple[int, int, int, int | None, list[int]] | None:
    for index, row in enumerate(sheet.rows[:50]):
        keys = [header_key(cell.text) for cell in row]
        ids = [i for i, key in enumerate(keys) if key in ID_HEADERS]
        names = [i for i, key in enumerate(keys) if key in NAME_HEADERS]
        if not ids or not names:
            continue
        if len(ids) > 1 or len(names) > 1:
            raise CollectionError(f"{sheet.name}:{index + 1}: 학번 또는 이름 열이 여러 개여서 선택할 수 없습니다.")
        status = next((i for i, key in enumerate(keys) if key in STATUS_HEADERS), None)
        url_cols = [i for i, key in enumerate(keys) if key in URL_HEADERS]
        if not url_cols:
            url_cols = [i for i in range(len(row)) if i not in {ids[0], names[0], status}]
        return index, ids[0], names[0], status, url_cols
    return None


def _at(row: list[Cell], index: int | None) -> Cell:
    return row[index] if index is not None and index < len(row) else Cell()


def _extend_unique(target: list[str], values: list[str], *, repositories: bool = False) -> None:
    def key(url: str) -> tuple:
        if repositories:
            return (url.lower(),)
        return submission_key(url)

    seen = {key(value) for value in target}
    for value in values:
        if key(value) not in seen:
            seen.add(key(value))
            target.append(value)


def collect(path: Path, *, sheet_name: str | None = None, include_unsubmitted: bool = False) -> Collection:
    return collect_sheets(read_excel(path, sheet_name), include_unsubmitted=include_unsubmitted)


def collect_sheets(sheets: list[Sheet], *, include_unsubmitted: bool = False) -> Collection:
    result = Collection()
    by_id: dict[str, Student] = {}
    matched_sheets = 0
    for sheet in sheets:
        header = _find_header(sheet)
        if header is None:
            result.warnings.append(Warning(sheet.name, None, "", "학번/아이디와 이름/성명 헤더를 찾지 못해 시트를 건너뛰었습니다."))
            continue
        matched_sheets += 1
        header_row, id_col, name_col, status_col, url_cols = header
        for row_index, row in enumerate(sheet.rows[header_row + 1:], header_row + 2):
            if not any(cell.text or cell.links for cell in row):
                continue
            student_id = normalize_text(_at(row, id_col).text).strip()
            name = _at(row, name_col).text.strip()
            status = _at(row, status_col).text.strip()
            if header_key(student_id) in ID_HEADERS and header_key(name) in NAME_HEADERS:
                continue  # Some exports repeat the header between pages.
            if not include_unsubmitted and header_key(status) in UNSUBMITTED:
                result.skipped_unsubmitted += 1
                continue
            texts = []
            for col in url_cols:
                cell = _at(row, col)
                texts.extend([cell.text, *cell.links])
            source_text = "\n".join(dict.fromkeys(text for text in texts if text))

            def exception(reason: str, value: str) -> None:
                result.exceptions.append(SubmissionException(sheet.name, row_index, student_id, name, reason, value))

            if header_key(status) in UNSUBMITTED:
                exception("not_submitted", source_text)
                continue
            if not student_id:
                exception("missing_student_id", source_text)
                continue
            if student_id.startswith("="):
                exception("invalid_student_id", source_text)
                continue
            if not name:
                exception("missing_name", source_text)
                continue
            extracted = extract_urls(texts)
            for link in extracted.rejected:
                exception(link.reason, link.value)
            if not extracted.urls:
                if not extracted.rejected:
                    exception("missing_repository_url", source_text)
                continue
            existing = by_id.get(student_id)
            if existing:
                if existing.name and name and normalize_text(existing.name) != normalize_text(name):
                    exception("conflicting_name", source_text)
                    continue
                if status and not existing.submission_status:
                    existing.submission_status = status
                _extend_unique(existing.repository_urls, extracted.repositories, repositories=True)
                _extend_unique(existing.submission_urls, extracted.urls)
            else:
                student = Student(student_id, name, extracted.repositories, extracted.urls, status)
                by_id[student_id] = student
                result.students.append(student)
    if not matched_sheets:
        raise CollectionError("학번/아이디와 이름/성명 열을 가진 시트가 없습니다. 첫 50행 안에 헤더가 있는지 확인하세요.")
    return result
