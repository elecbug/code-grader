"""Git transport and revision lookup. No submitted programs are executed."""

from datetime import datetime
from pathlib import Path
import re
import subprocess

from .auth import GitCredentials


class GitError(RuntimeError):
    pass


class GitClient:
    def __init__(self, credentials: GitCredentials | None = None, timeout: float = 120.0):
        self.credentials = credentials or GitCredentials()
        self.timeout = timeout

    def run(self, *args: str, repo: Path | None = None) -> str:
        command = ["git"] + (["-C", str(repo)] if repo is not None else []) + list(args)
        try:
            process = subprocess.run(command, env=self.credentials.environment(), capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=self.timeout)
        except FileNotFoundError as exc:
            raise GitError("git 명령을 찾을 수 없습니다. Git을 설치하세요.") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"Git 작업이 {self.timeout:g}초 안에 완료되지 않았습니다.") from exc
        if process.returncode:
            message = self.credentials.redact(process.stderr or process.stdout).strip()
            raise GitError(message[:2000] or f"Git 작업 실패 (종료 코드 {process.returncode})")
        return process.stdout.strip()

    def clone(self, url: str, destination: Path) -> None:
        self.run("clone", "--no-checkout", "--", url, str(destination))

    def refs(self, repo: Path) -> dict[str, str]:
        refs = {}
        output = self.run("for-each-ref", "--format=%(refname)", "refs/remotes/origin", "refs/tags", repo=repo)
        for name in output.splitlines():
            if name == "refs/remotes/origin/HEAD":
                continue
            short = name.removeprefix("refs/remotes/origin/").removeprefix("refs/tags/")
            # Prefer a branch when it has the same name as a tag.
            refs.setdefault(short, name)
        return refs

    def default_ref(self, repo: Path) -> tuple[str, str]:
        ref = self.run("symbolic-ref", "refs/remotes/origin/HEAD", repo=repo)
        return ref.removeprefix("refs/remotes/origin/"), ref

    def resolve_commit(self, repo: Path, ref: str, limit: datetime | None = None) -> str:
        if limit is None:
            return self.run("rev-parse", "--verify", f"{ref}^{{commit}}", repo=repo)
        history = self.run("rev-list", "--date-order", "--timestamp", ref, repo=repo)
        cutoff = limit.timestamp()
        for entry in history.splitlines():
            timestamp, commit = entry.split(" ", 1)
            if int(timestamp) <= cutoff:
                return commit
        raise GitError("마감 시각 이하의 커밋이 없습니다.")

    def resolve_path_ref(self, tail: str, refs: dict[str, str]) -> tuple[str, str, str]:
        parts = tail.split("/")
        for index in range(len(parts), 0, -1):
            candidate = "/".join(parts[:index])
            if candidate in refs:
                return candidate, refs[candidate], "/".join(parts[index:])
        if re.fullmatch(r"[a-fA-F0-9]{7,64}", parts[0]):
            return parts[0], parts[0], "/".join(parts[1:])
        raise GitError("제출 URL에 지정한 브랜치나 태그를 찾을 수 없습니다.")

    def path_type(self, repo: Path, commit: str, path: str) -> str:
        return self.run("cat-file", "-t", f"{commit}:{path}", repo=repo)

    def ensure_commit(self, repo: Path, commit: str) -> str:
        try:
            return self.resolve_commit(repo, commit)
        except GitError:
            # A force push may have removed the deadline commit from current
            # branch history. Ask the same origin for that exact object.
            self.run("fetch", "--no-tags", "origin", commit, repo=repo)
            return self.resolve_commit(repo, commit)

    def checkout(self, repo: Path, commit: str) -> None:
        self.run("checkout", "--detach", commit, repo=repo)

    def worktree(self, repo: Path, destination: Path, commit: str) -> None:
        self.run("worktree", "add", "--detach", str(destination), commit, repo=repo)
