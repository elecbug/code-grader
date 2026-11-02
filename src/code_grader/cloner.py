"""Plan and clone every submitted repository, retaining branch/path provenance."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import unquote, urlsplit

from .git_client import GitClient, GitError
from .models import CollectionError
from .urls import normalize_text, normalize_url, repository_url, submission_key


@dataclass
class Submission:
    url: str
    kind: str
    tail: str = ""


@dataclass
class RepositoryPlan:
    student_id: str
    name: str
    repository_url: str
    submissions: list[Submission] = field(default_factory=list)

    def directory(self, output: Path) -> Path:
        owner, repo = urlsplit(self.repository_url).path.strip("/").split("/")
        return output / self.student_id / owner / repo


def parse_limit(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        limit = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if limit.tzinfo is None or limit.utcoffset() is None:
            raise ValueError
        return limit.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CollectionError("마감 시각은 시간대를 포함한 ISO 8601 형식이어야 합니다. 예: 2026-11-02T23:59:59+09:00") from exc


def load_collection(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise CollectionError("collect 명령이 생성한 JSON 파일이 필요합니다.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("students"), list):
        raise CollectionError("입력 JSON에 students 배열이 필요합니다.")
    if not isinstance(data.get("exceptions", []), list):
        raise CollectionError("입력 JSON의 exceptions는 배열이어야 합니다.")
    return data


def parse_submission(value: str) -> tuple[str, Submission]:
    url = normalize_url(normalize_text(value).strip())
    if not url or urlsplit(url).netloc != "github.com":
        raise ValueError("non_github_url")
    repo = repository_url(url)
    if not repo:
        raise ValueError("invalid_github_url")
    parts = urlsplit(url).path.strip("/").split("/")
    if len(parts) == 2:
        return repo, Submission(url, "repository")
    if parts[2] not in {"blob", "tree"} or len(parts) < 4 or not parts[3]:
        raise ValueError("unsupported_submission_url")
    tail = unquote("/".join(parts[3:]))
    if any(part in {".", "..", ""} for part in tail.split("/")) or "\x00" in tail or "\\" in tail:
        raise ValueError("invalid_submission_path")
    return repo, Submission(url, "file" if parts[2] == "blob" else "directory", tail)


def build_plan(data: dict) -> tuple[list[RepositoryPlan], list[dict]]:
    groups: dict[tuple[str, str], RepositoryPlan] = {}
    exceptions = []
    seen: dict[tuple[str, str], set[tuple]] = {}
    for student in data["students"]:
        if not isinstance(student, dict):
            raise CollectionError("students의 각 항목은 객체여야 합니다.")
        student_id, name = student.get("student_id"), student.get("name", "")
        if not isinstance(student_id, str) or not isinstance(name, str):
            raise CollectionError("student_id와 name은 문자열이어야 합니다.")
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", student_id)
                or student_id == "clone-report.json"):
            exceptions.append(dict(student_id=student_id, name=name, url="", reason="invalid_student_id", detail="학번을 안전한 디렉터리 이름으로 사용할 수 없습니다."))
            continue
        # Original submission links carry the branch and path. Canonical repo
        # URLs are a fallback only when no original links were supplied.
        urls = student.get("submission_urls") or student.get("repository_urls") or []
        if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
            raise CollectionError("submission_urls와 repository_urls는 문자열 배열이어야 합니다.")
        if not urls:
            exceptions.append(dict(student_id=student_id, name=name, url="", reason="missing_repository_url", detail="클론할 제출 주소가 없습니다."))
        for url in urls:
            try:
                repo, submission = parse_submission(url)
            except ValueError as exc:
                exceptions.append(dict(student_id=student_id, name=name, url=url, reason=str(exc), detail="지원하는 제출은 일반 GitHub 저장소 루트, blob 파일, tree 디렉터리 URL입니다."))
                continue
            key = (student_id, repo.lower())
            if key not in groups:
                groups[key] = RepositoryPlan(student_id, name, repo)
                seen[key] = set()
            if groups[key].name != name:
                exceptions.append(dict(student_id=student_id, name=name, url=url, reason="conflicting_name", detail="같은 학번에 다른 이름이 있습니다."))
                continue
            url_key = submission_key(submission.url)
            if url_key not in seen[key]:
                seen[key].add(url_key)
                groups[key].submissions.append(submission)
    return list(groups.values()), exceptions


def _resolve_submission(git: GitClient, repo: Path, submission: Submission,
                        refs: dict[str, str], limit: datetime | None) -> dict:
    if submission.kind == "repository":
        label, ref = git.default_ref(repo)
        path = ""
    else:
        label, ref, path = git.resolve_path_ref(submission.tail, refs)
    commit = git.resolve_commit(repo, ref, limit)
    if submission.kind == "file" and not path:
        raise GitError("파일 제출 URL에 파일 경로가 없습니다.")
    if path:
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise GitError("제출 파일 경로가 올바르지 않습니다.")
        actual = git.path_type(repo, commit, path)
        expected = "blob" if submission.kind == "file" else "tree"
        if actual != expected:
            raise GitError(f"제출 경로의 유형이 일치하지 않습니다: {path}")
    return dict(url=submission.url, kind=submission.kind, ref=label, commit=commit, path=path)


def clone_repositories(plans: list[RepositoryPlan], output: Path, git: GitClient,
                       *, limit: datetime | None = None, dry_run: bool = False,
                       exceptions: list[dict] | None = None, input_exceptions: list | None = None) -> dict:
    output = output.resolve()
    report = dict(limit=limit.isoformat() if limit else None, repositories=[],
                  exceptions=list(exceptions or []), input_exceptions=list(input_exceptions or []))
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
    for plan in plans:
        destination = plan.directory(output)
        item = dict(student_id=plan.student_id, name=plan.name, repository_url=plan.repository_url,
                    directory=str(destination), status="planned", submissions=[asdict(s) for s in plan.submissions])
        report["repositories"].append(item)
        if dry_run:
            continue
        item["submissions"] = []
        failures_before = len(report["exceptions"])

        def exception(reason: str, detail: str, url: str = plan.repository_url) -> None:
            report["exceptions"].append(dict(student_id=plan.student_id, name=plan.name,
                                             url=url, reason=reason, detail=git.credentials.redact(detail)))

        if destination.exists() or destination.is_symlink():
            item["status"] = "skipped"
            exception("destination_exists", "기존 폴더를 보존했습니다. 다른 출력 폴더를 지정하세요.")
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".clone-", dir=destination.parent) as temporary:
                staging = Path(temporary)
                source = staging / "repository"
                git.clone(plan.repository_url + ".git", source)
                refs = git.refs(source)
                resolved = []
                for submission in plan.submissions:
                    try:
                        resolved.append(_resolve_submission(git, source, submission, refs, limit))
                    except GitError as exc:
                        exception("submission_resolution_failed", str(exc), submission.url)
                if not resolved:
                    item["status"] = "failed"
                    continue
                git.checkout(source, resolved[0]["commit"])
                # No worktrees exist yet, so moving the new clone is safe.
                staging.rename(destination)
            source = destination / "repository"
            checkouts = {resolved[0]["commit"]: source}
            for submission in resolved:
                commit = submission["commit"]
                if commit not in checkouts:
                    target = destination / "checkouts" / commit
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        git.worktree(source, target, commit)
                        checkouts[commit] = target
                    except GitError as exc:
                        exception("checkout_failed", str(exc), submission["url"])
                        continue
                submission["checkout"] = str(checkouts[commit])
                item["submissions"].append(submission)
            item["status"] = "partial" if len(report["exceptions"]) > failures_before else "cloned"
            (destination / "submission.json").write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (GitError, OSError) as exc:
            item["status"] = "failed"
            exception("clone_failed", str(exc))
    report["summary"] = dict(
        repositories=len(report["repositories"]),
        cloned=sum(item["status"] == "cloned" for item in report["repositories"]),
        partial=sum(item["status"] == "partial" for item in report["repositories"]),
        failed=sum(item["status"] in {"failed", "skipped"} for item in report["repositories"]),
        exceptions=len(report["exceptions"]),
    )
    return report
