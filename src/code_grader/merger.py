"""Combine source files using lang-grader's merge.sh output format."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tempfile
from typing import TextIO

from .models import CollectionError


DEFAULT_EXTENSIONS = (".c", ".h", ".md")
DEFAULT_OUTPUT = "merged_output.txt"
SKIP_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".pytest_cache", ".mypy_cache"}


@dataclass
class MergeResult:
    directory: Path
    output: Path | None
    files: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)


def normalize_extensions(values: list[str] | tuple[str, ...]) -> set[str]:
    result = {value.lower() if value.startswith(".") else "." + value.lower() for value in values}
    if not result or any(not re.fullmatch(r"\.[a-z0-9]+", value) for value in result):
        raise CollectionError("확장자는 .c .h .md 또는 c h md처럼 지정하세요.")
    return result


def _root(directory: Path) -> Path:
    if not directory.is_dir():
        raise CollectionError(f"병합할 디렉터리를 찾을 수 없습니다: {directory}")
    return directory.resolve()


def _scan(root: Path, extensions: set[str], output: Path | None = None) -> tuple[list[Path], list[tuple[Path, str]]]:
    files = []
    errors = []

    def onerror(error: OSError) -> None:
        errors.append((Path(error.filename) if error.filename else root, str(error)))

    for current, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        current = Path(current)
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRECTORIES and not (current / name).is_symlink())
        for name in names:
            path = current / name
            if (name == DEFAULT_OUTPUT or path == output or path.is_symlink()
                    or path.suffix.lower() not in extensions):
                continue
            # os.walk also lists special files; merge regular files only.
            if path.is_file():
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix()), errors


def _read_source(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeError as exc:
            raise ValueError("UTF-16 텍스트를 해석하지 못했습니다.") from exc
    sample = data[:4096]
    controls = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    if b"\x00" in data or controls / max(1, len(sample)) > 0.05:
        raise ValueError("텍스트가 아닌 파일이라 병합하지 않았습니다.")
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError("UTF-8, CP949, EUC-KR 텍스트로 해석하지 못했습니다.")


def _write_files(root: Path, files: list[Path], stream: TextIO, result: MergeResult) -> None:
    for path in files:
        try:
            text = _read_source(path)
        except (OSError, ValueError) as exc:
            result.errors.append((path, str(exc)))
            continue
        label = path.relative_to(root).as_posix().replace("\r", "\\r").replace("\n", "\\n")
        stream.write(f"# {label}\n\n")
        stream.write(text)
        # Match merge.sh's two echo calls after cat, preserving file contents.
        stream.write("\n\n")
        result.files += 1


def _merge_files(root: Path, files: list[Path], output: Path | None, stream: TextIO | None,
                 errors: list[tuple[Path, str]] | None = None) -> MergeResult:
    result = MergeResult(root, output, errors=list(errors or []))
    if stream is not None:
        _write_files(root, files, stream, result)
        return result
    if output is None:
        raise CollectionError("결과 파일 또는 출력 스트림이 필요합니다.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", prefix=".merge-",
                                         suffix=".tmp", dir=output.parent, delete=False) as target:
            temporary = Path(target.name)
            _write_files(root, files, target, result)
        # Replace the output itself; never follow an existing output symlink.
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def merge_directory(directory: Path, *, output: Path | None = None, stream: TextIO | None = None,
                    extensions: list[str] | tuple[str, ...] = DEFAULT_EXTENSIONS) -> MergeResult:
    root = _root(directory)
    if stream is not None and output is not None:
        raise CollectionError("파일 저장과 표준 출력은 동시에 지정할 수 없습니다.")
    if stream is None:
        output = Path(os.path.abspath(output)) if output is not None else root / DEFAULT_OUTPUT
    files, errors = _scan(root, normalize_extensions(extensions), output)
    return _merge_files(root, files, output, stream, errors)


def merge_all(directory: Path, *, extensions: list[str] | tuple[str, ...] = DEFAULT_EXTENSIONS) -> tuple[list[MergeResult], list[tuple[Path, str]]]:
    root = _root(directory)
    files, scan_errors = _scan(root, normalize_extensions(extensions))
    groups: dict[Path, list[Path]] = {}
    for path in files:
        parent = path.parent
        while True:
            groups.setdefault(parent, []).append(path)
            if parent == root:
                break
            parent = parent.parent
    results = []
    for folder, sources in sorted(groups.items(), key=lambda item: item[0].relative_to(root).as_posix()):
        try:
            results.append(_merge_files(folder, sources, folder / DEFAULT_OUTPUT, None))
        except OSError as exc:
            results.append(MergeResult(folder, folder / DEFAULT_OUTPUT, errors=[(folder, str(exc))]))
    return results, scan_errors
