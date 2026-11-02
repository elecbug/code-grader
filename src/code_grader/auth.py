"""Load GitHub credentials without expanding or executing .env values."""

import base64
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values

from .models import CollectionError


def load_token(env_file: Path | None = None) -> str | None:
    path = env_file if env_file is not None else Path.cwd() / ".env"
    if env_file is not None and not path.is_file():
        raise CollectionError(f".env 파일을 찾을 수 없습니다: {path}")
    try:
        values = dotenv_values(path, interpolate=False, encoding="utf-8-sig") if path.is_file() else {}
    except (OSError, UnicodeError) as exc:
        raise CollectionError(".env 파일을 UTF-8 텍스트로 읽지 못했습니다.") from exc
    token = (os.environ.get("GITHUB_TOKEN") or values.get("GITHUB_TOKEN") or "").strip()
    if token and (not token.isascii() or any(char.isspace() for char in token)):
        raise CollectionError("GITHUB_TOKEN에는 공백이나 줄바꿈을 포함할 수 없습니다.")
    return token or None


class GitCredentials:
    def __init__(self, token: str | None = None):
        self._token = token
        self._encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode() if token else ""

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        # Trace output could include request headers from the environment.
        for key in list(env):
            if key.startswith("GIT_TRACE") or key == "GIT_CURL_VERBOSE":
                env.pop(key)
        config = [("credential.helper", ""), ("core.hooksPath", os.devnull)]
        if self._token:
            config.append(("http.https://github.com/.extraheader", f"Authorization: Basic {self._encoded}"))
        try:
            count = int(env.get("GIT_CONFIG_COUNT", "0"))
            if count < 0:
                raise ValueError
        except ValueError as exc:
            raise CollectionError("GIT_CONFIG_COUNT 환경변수가 올바르지 않습니다.") from exc
        for key, value in config:
            env[f"GIT_CONFIG_KEY_{count}"] = key
            env[f"GIT_CONFIG_VALUE_{count}"] = value
            count += 1
        env["GIT_CONFIG_COUNT"] = str(count)
        return env

    def redact(self, text: str) -> str:
        if self._token:
            for secret in (self._token, self._encoded, quote(self._token, safe="")):
                text = text.replace(secret, "[REDACTED]")
        return text
