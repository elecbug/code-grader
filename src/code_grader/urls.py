"""Extract explicit GitHub links without guessing missing owners or repositories.

NFKC normalization and trailing-delimiter cleanup build on lang-grader's
script/make_student_map.py. Other hosts are retained only as rejected submissions.
"""

from dataclasses import dataclass, field
import html
import re
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit


HOST_PATTERN = r"(?:(?:gist\.)?github\.com|(?:raw|gist)\.githubusercontent\.com)"
LINK_PATTERN = re.compile(
    r"(?:https?://"
    r"|git@github\.com:"
    rf"|(?<![\w@./-])(?:www\.)?{HOST_PATTERN}/"
    r"|(?<![\w@./-])www\.)"
    r"(?:(?!https?://|git@github\.com:)[^\s<>\"'`])+",
    re.IGNORECASE,
)
GITHUB_MENTION = re.compile(r"(?<![\w-])github(?:usercontent)?\s*\.\s*com", re.IGNORECASE)
OWNER_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,38}\Z")
REPO_PATTERN = re.compile(r"[a-zA-Z0-9_.-]+\Z")
# These are GitHub site routes, not repository owners in a submitted repo URL.
SITE_ROUTES = {
    "about", "account", "apps", "collections", "contact", "dashboard", "enterprise",
    "events", "explore", "features", "issues", "join", "login", "logout", "marketplace",
    "new", "notifications", "orgs", "organizations", "pricing", "pulls", "search",
    "security", "sessions", "settings", "site", "sponsors", "topics", "trending", "users",
}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"(https?)\s*:\s*/\s*/\s*", r"\1://", text, flags=re.IGNORECASE)


def clean_url(url: str) -> str:
    url = url.rstrip(";,.!?:、。…")
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    while url:
        previous = url
        for opening, closing in pairs:
            if url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        url = url.rstrip(";,.!?:、。…")
        if url == previous:
            break
    return url


def normalize_url(candidate: str) -> str | None:
    candidate = clean_url(candidate)
    if candidate.lower().startswith("git@github.com:"):
        candidate = "https://github.com/" + candidate.split(":", 1)[1]
    elif not candidate.lower().startswith(("http://", "https://")):
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
        host = parts.netloc.lower()
        if not parts.hostname or parts.scheme.lower() not in {"http", "https"}:
            return None
        if host == "www.github.com":
            host = "github.com"
        scheme = "https" if host == "github.com" else parts.scheme.lower()
        return urlunsplit((scheme, host, parts.path, parts.query, parts.fragment))
    except ValueError:
        return None


def repository_url(url: str) -> str | None:
    """Reduce regular GitHub links to https://github.com/owner/repo."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host != "github.com":
        return None
    path = parts.path.strip("/").split("/")
    if len(path) < 2:
        return None
    owner, repo = unquote(path[0]), unquote(path[1])
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if (not OWNER_PATTERN.fullmatch(owner) or not REPO_PATTERN.fullmatch(repo)
            or repo in {".", ".."} or owner.lower() in SITE_ROUTES):
        return None
    return f"https://github.com/{owner}/{repo}"


def submission_key(url: str) -> tuple:
    parts = urlsplit(url)
    if parts.netloc != "github.com":
        return (url,)
    segments = parts.path.split("/")
    return (parts.netloc, "/".join(segments[:3]).lower(), tuple(segments[3:]), parts.query, parts.fragment)


@dataclass
class RejectedLink:
    value: str
    reason: str


@dataclass
class ExtractedLinks:
    urls: list[str] = field(default_factory=list)
    repositories: list[str] = field(default_factory=list)
    rejected: list[RejectedLink] = field(default_factory=list)


def extract_urls(texts: list[str]) -> ExtractedLinks:
    """Collect only regular GitHub links; preserve every other URL as an exception.

    Original casing is preserved; duplicate links differing only in
    owner/repository case are merged. Branch/path case remains significant.
    """
    result = ExtractedLinks()
    seen_urls: set[tuple] = set()
    seen_repos: set[str] = set()
    rejected: set[tuple[str, str]] = set()

    def reject(value: str, reason: str) -> None:
        if (value, reason) not in rejected:
            rejected.add((value, reason))
            result.rejected.append(RejectedLink(value, reason))

    for text in texts:
        text = normalize_text(text)
        matches = list(LINK_PATTERN.finditer(text))
        for match in matches:
            url = normalize_url(match.group())
            if not url:
                reject(clean_url(match.group()), "invalid_url")
                continue
            host = urlsplit(url).netloc
            repo = repository_url(url)
            if host != "github.com":
                reject(url, "non_github_url")
                continue
            if not repo:
                reject(url, "invalid_github_url")
                continue
            key = submission_key(url)
            if key not in seen_urls:
                seen_urls.add(key)
                result.urls.append(url)
            if repo and repo.lower() not in seen_repos:
                seen_repos.add(repo.lower())
                result.repositories.append(repo)
        for mention in GITHUB_MENTION.finditer(text):
            if not any(match.start() <= mention.start() < match.end() for match in matches):
                reject(text[mention.start():].splitlines()[0], "invalid_github_url")
    return result
