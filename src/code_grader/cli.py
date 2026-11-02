import argparse
from dataclasses import asdict
import csv
import io
import json
from pathlib import Path
import sys

from . import __version__
from .collector import collect
from .models import Collection, CollectionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="엑셀 과제 제출 목록에서 학번, 이름, GitHub 주소를 수집합니다.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help=".xls/.xlsx 제출 목록 정리")
    collect_parser.add_argument("input", type=Path, help="과제 제출 목록 엑셀 파일")
    collect_parser.add_argument("-o", "--output", type=Path, help="결과 파일 (생략하면 표준 출력)")
    collect_parser.add_argument("--format", choices=("json", "csv"), help="출력 형식 (기본 JSON, .csv 출력 파일은 CSV)")
    collect_parser.add_argument("--sheet", help="처리할 시트 이름 (기본: 헤더가 있는 모든 시트)")
    collect_parser.add_argument("--include-unsubmitted", action="store_true", help="미제출 학생도 결과에 포함")
    collect_parser.add_argument("--exceptions-output", type=Path, help="예외 목록을 별도 JSON으로 저장 (CSV 파일 출력 시 기본: <이름>.exceptions.json)")
    return parser


def render(result: Collection, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["student_id", "name", "repository_urls", "submission_urls", "submission_status"])
    for student in result.students:
        writer.writerow([
            student.student_id, student.name, "\n".join(student.repository_urls),
            "\n".join(student.submission_urls), student.submission_status,
        ])
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_format = args.format or ("csv" if args.output and args.output.suffix.lower() == ".csv" else "json")
        exceptions_output = args.exceptions_output
        if exceptions_output is None and args.output and output_format == "csv":
            exceptions_output = args.output.with_name(args.output.stem + ".exceptions.json")
        paths = [path.resolve() for path in (args.input, args.output, exceptions_output) if path is not None]
        if len(paths) != len(set(paths)):
            raise CollectionError("입력 엑셀, 출력 파일, 예외 파일은 서로 다른 경로여야 합니다.")
        result = collect(args.input, sheet_name=args.sheet, include_unsubmitted=args.include_unsubmitted)
        text = render(result, output_format)
        if exceptions_output:
            exceptions_output.parent.mkdir(parents=True, exist_ok=True)
            exceptions_output.write_text(json.dumps([asdict(item) for item in result.exceptions], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # BOM lets Excel recognize Korean UTF-8 CSV files.
            with args.output.open("w", encoding="utf-8-sig" if output_format == "csv" else "utf-8", newline="") as output:
                output.write(text)
        else:
            sys.stdout.write(text)
    except (CollectionError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    repo_count = sum(len(student.repository_urls) for student in result.students)
    link_count = sum(len(student.submission_urls) for student in result.students)
    print(f"정상 학생 {len(result.students)}명, 저장소 {repo_count}개, 제출 링크 {link_count}개, 예외 {len(result.exceptions)}건, 미제출 제외 {result.skipped_unsubmitted}행", file=sys.stderr)
    for item in result.exceptions:
        print(f"예외 [{item.reason}]: {item.sheet}:{item.row} [{item.student_id}] {item.name}", file=sys.stderr)
    for warning in result.warnings:
        where = warning.sheet + (f":{warning.row}" if warning.row else "")
        student = f" [{warning.student_id}]" if warning.student_id else ""
        code = f" [{warning.code}]" if warning.code else ""
        print(f"경고{code}: {where}{student}: {warning.message}", file=sys.stderr)
    return 0
