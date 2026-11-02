import argparse
from dataclasses import asdict
import csv
import io
import json
from pathlib import Path
import sys

from . import __version__
from .auth import GitCredentials, load_token
from .cloner import build_plan, clone_repositories, load_collection, parse_limit
from .collector import collect
from .git_client import GitClient
from .models import Collection, CollectionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="엑셀 과제 제출 정보를 정리하고 GitHub 저장소를 클론합니다.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help=".xls/.xlsx 제출 목록 정리")
    collect_parser.add_argument("input", type=Path, help="과제 제출 목록 엑셀 파일")
    collect_parser.add_argument("-o", "--output", type=Path, help="결과 파일 (생략하면 표준 출력)")
    collect_parser.add_argument("--format", choices=("json", "csv"), help="출력 형식 (기본 JSON, .csv 출력 파일은 CSV)")
    collect_parser.add_argument("--sheet", help="처리할 시트 이름 (기본: 헤더가 있는 모든 시트)")
    collect_parser.add_argument("--include-unsubmitted", action="store_true", help="미제출 학생도 결과에 포함")
    collect_parser.add_argument("--exceptions-output", type=Path, help="예외 목록을 별도 JSON으로 저장 (CSV 파일 출력 시 기본: <이름>.exceptions.json)")
    clone_parser = commands.add_parser("clone", help="수집한 JSON의 GitHub 저장소 클론")
    clone_parser.add_argument("input", type=Path, help="collect 명령이 생성한 JSON 파일")
    clone_parser.add_argument("-o", "--output", type=Path, default=Path("output/repositories"), help="클론 결과 디렉터리 (기본: output/repositories)")
    clone_parser.add_argument("--env-file", type=Path, help="토큰을 읽을 .env 경로 (기본: 현재 디렉터리의 .env)")
    clone_parser.add_argument("--limit", help="마감 시각 (시간대 포함 ISO 8601, 생략하면 입력 JSON의 limit 또는 최신 커밋)")
    clone_parser.add_argument("--limit-basis", choices=("commit", "push"), default="commit",
                              help="마감 기준: commit=커미터 시각, push=GitHub에 푸시된 시각 (기본: commit)")
    clone_parser.add_argument("--timeout", type=float, default=120.0, help="Git 작업별 제한 시간, 초 (기본: 120)")
    clone_parser.add_argument("--report", type=Path, help="클론 리포트 경로 (기본: 출력 디렉터리/clone-report.json)")
    clone_parser.add_argument("--dry-run", action="store_true", help="네트워크 접속과 클론 없이 처리 계획만 출력")
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
        if args.command == "clone":
            return run_clone(args)
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


def run_clone(args: argparse.Namespace) -> int:
    if not 0 < args.timeout < float("inf"):
        raise CollectionError("Git 제한 시간은 0보다 큰 유한한 수여야 합니다.")
    report_path = args.report or (args.output / "clone-report.json")
    if args.input.resolve() in {args.output.resolve(), report_path.resolve()}:
        raise CollectionError("입력 JSON과 클론 출력 경로는 서로 달라야 합니다.")
    env_file = args.env_file or Path.cwd() / ".env"
    if report_path.resolve() == env_file.resolve():
        raise CollectionError("클론 리포트로 .env 파일을 덮어쓸 수 없습니다.")
    data = load_collection(args.input)
    limit = parse_limit(args.limit if args.limit is not None else data.get("limit"))
    plans, exceptions = build_plan(data)
    git = GitClient(GitCredentials(load_token(args.env_file)), args.timeout)
    result = clone_repositories(plans, args.output, git, limit=limit, dry_run=args.dry_run,
                                exceptions=exceptions, input_exceptions=data.get("exceptions", []),
                                limit_basis=args.limit_basis)
    if not args.dry_run or args.report:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    summary = result["summary"]
    print(f"저장소 {summary['repositories']}개, 클론 완료 {summary['cloned']}개, 일부 완료 {summary['partial']}개, 실패/건너뜀 {summary['failed']}개, 예외 {summary['exceptions']}건", file=sys.stderr)
    for exception in result["exceptions"]:
        print(f"예외 [{exception['reason']}]: [{exception['student_id']}] {exception['url']}", file=sys.stderr)
    return 1 if result["exceptions"] else 0
