"""Read both Excel formats while preserving hyperlink targets and numeric IDs."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import re

from .models import CollectionError


@dataclass
class Cell:
    text: str = ""
    links: list[str] = field(default_factory=list)


@dataclass
class Sheet:
    name: str
    rows: list[list[Cell]]


def cell_text(value: object, number_format: str = "") -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, int) or value.is_integer():
            text = str(int(value))
            if re.fullmatch(r"0+", number_format):
                text = text.zfill(len(number_format))
            return text
    return str(value).strip()


def read_excel(path: Path, sheet_name: str | None = None) -> list[Sheet]:
    suffix = path.suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise CollectionError("지원하는 파일 형식은 .xls, .xlsx입니다.")
    if not path.is_file():
        raise CollectionError(f"엑셀 파일을 찾을 수 없습니다: {path}")
    try:
        data = path.read_bytes()
        if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return _read_xls(path, sheet_name)
        if data.startswith(b"PK\x03\x04"):
            return _read_xlsx(data, sheet_name)
        from .html_excel import read_html_excel
        return read_html_excel(data, sheet_name)
    except CollectionError:
        raise
    except Exception as exc:
        raise CollectionError(f"엑셀 파일을 읽지 못했습니다: {path} ({exc})") from exc


def _select_names(names: list[str], selected: str | None) -> list[str]:
    if selected is None:
        return names
    if selected not in names:
        raise CollectionError(f"시트를 찾을 수 없습니다: {selected}. 사용 가능한 시트: {', '.join(names)}")
    return [selected]


def _read_xls(path: Path, selected: str | None) -> list[Sheet]:
    import xlrd

    book = xlrd.open_workbook(str(path), formatting_info=True, on_demand=True)
    try:
        result = []
        for name in _select_names(book.sheet_names(), selected):
            source = book.sheet_by_name(name)
            links: dict[tuple[int, int], list[str]] = {}
            for link in source.hyperlink_list:
                if link.url_or_path:
                    for row in range(link.frowx, link.lrowx + 1):
                        for col in range(link.fcolx, link.lcolx + 1):
                            links.setdefault((row, col), []).append(link.url_or_path)
            rows = []
            for row in range(source.nrows):
                cells = []
                for col in range(source.ncols):
                    cell = source.cell(row, col)
                    fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                    cells.append(Cell(cell_text(cell.value, fmt), links.get((row, col), [])))
                rows.append(cells)
            result.append(Sheet(name, rows))
        return result
    finally:
        book.release_resources()


def _read_xlsx(data: bytes, selected: str | None) -> list[Sheet]:
    from openpyxl import load_workbook

    # Normal mode retains hyperlinks; data_only=False also keeps HYPERLINK formulas.
    book = load_workbook(BytesIO(data), read_only=False, data_only=False)
    try:
        result = []
        for name in _select_names(book.sheetnames, selected):
            rows = []
            for row in book[name].iter_rows():
                cells = []
                for cell in row:
                    target = cell.hyperlink.target if cell.hyperlink else None
                    cells.append(Cell(cell_text(cell.value, cell.number_format), [target] if target else []))
                rows.append(cells)
            result.append(Sheet(name, rows))
        return result
    finally:
        book.close()
