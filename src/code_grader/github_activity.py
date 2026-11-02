"""Select the branch state accepted by GitHub before a submission deadline.

Uses /repos/{owner}/{repo}/activity, not commit timestamps or the limited
Events API. Source: https://docs.github.com/en/rest/repos/repos#list-repository-activities
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .auth import GitCredentials
from .git_client import GitError
from .urls import repository_url


class PushLookupError(GitError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _check_api_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "api.github.com":
        raise PushLookupError("push_api_failed", "GitHub API 외의 주소로 인증 정보를 전달할 수 없습니다.")


class _GitHubRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_api_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class PushState:
    commit: str
    pushed_at: str
    activity_id: str
    ref: str
    push_type: str

    def to_dict(self) -> dict:
        return asdict(self)


class GitHubActivityClient:
    def __init__(self, credentials: GitCredentials, timeout: float = 120.0):
        self.credentials = credentials
        self.timeout = timeout
        self.opener = build_opener(_GitHubRedirectHandler())
        self._cache: dict[tuple, PushState | PushLookupError] = {}

    def _page(self, url: str) -> tuple[list, str | None]:
        _check_api_url(url)
        request = Request(url, headers=self.credentials.api_headers())
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = json.load(response)
                links = response.headers.get("Link", "")
        except HTTPError as exc:
            raise PushLookupError("push_api_failed", f"GitHub 활동 API 오류: HTTP {exc.code}. 토큰 권한과 API 호출 한도를 확인하세요.") from exc
        except (OSError, ValueError) as exc:
            raise PushLookupError("push_api_failed", "GitHub 활동 API 응답을 읽지 못했습니다.") from exc
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise PushLookupError("push_history_invalid", "GitHub 활동 API 응답이 활동 목록 형식이 아닙니다.")
        next_url = next((urljoin(url, target) for target, rel in re.findall(r'<([^>]+)>\s*;\s*rel="([^"]+)"', links)
                         if "next" in rel.split()), None)
        return data, next_url

    def last_push(self, repo_url: str, ref: str, limit: datetime) -> PushState:
        key = (repo_url.lower(), ref, limit.isoformat())
        if key not in self._cache:
            try:
                self._cache[key] = self._last_push(repo_url, ref, limit)
            except PushLookupError as exc:
                self._cache[key] = exc
        result = self._cache[key]
        if isinstance(result, PushLookupError):
            raise result
        return result

    def _last_push(self, repo_url: str, ref: str, limit: datetime) -> PushState:
        if repository_url(repo_url) != repo_url:
            raise PushLookupError("push_api_failed", "일반 GitHub 저장소 주소가 필요합니다.")
        if not ref.startswith(("refs/heads/", "refs/tags/")):
            raise PushLookupError("push_ref_required", "푸시 시각 기준에는 브랜치나 태그 URL이 필요합니다. 커밋 SHA만 지정한 URL은 푸시 이력을 특정할 수 없습니다.")
        path = urlsplit(repo_url).path
        query = urlencode({"ref": ref, "direction": "desc", "per_page": 100})
        url = f"https://api.github.com/repos{path}/activity?{query}"
        visited = set()
        while url:
            if url in visited:
                raise PushLookupError("push_history_invalid", "GitHub 활동 API 페이지가 순환합니다.")
            visited.add(url)
            activities, url = self._page(url)
            # The API returns repository activities in descending time order.
            for activity in activities:
                if activity.get("ref") != ref:
                    continue
                try:
                    # Current API responses use timestamp/activity_type; older
                    # responses and documentation examples use pushed_at/push_type.
                    timestamp = activity.get("timestamp", activity.get("pushed_at"))
                    pushed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if pushed_at.tzinfo is None or pushed_at.utcoffset() is None:
                        raise ValueError
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    raise PushLookupError("push_history_invalid", "활동 기록의 푸시 시각을 확인할 수 없습니다.") from exc
                if pushed_at > limit:
                    continue
                commit = activity.get("after")
                activity_type = activity.get("activity_type", activity.get("push_type", ""))
                if activity_type == "branch_deletion" or (isinstance(commit, str) and commit and set(commit) == {"0"}):
                    raise PushLookupError("ref_deleted_at_limit", "마감 시각 당시 제출 브랜치가 삭제된 상태였습니다.")
                if not isinstance(commit, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", commit):
                    raise PushLookupError("push_history_invalid", "푸시 직후의 커밋 SHA를 확인할 수 없습니다.")
                return PushState(commit, pushed_at.astimezone(timezone.utc).isoformat(),
                                 str(activity.get("id", "")), ref, str(activity_type))
        raise PushLookupError("push_history_unavailable", "마감 시각 이하의 푸시 기록을 확인하지 못했습니다. 커밋 시각으로 대체하지 않습니다.")
