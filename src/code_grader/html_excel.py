"""Read LMS exports that contain HTML tables despite their .xls extension."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re

from .excel import Cell, Sheet
from .models import CollectionError


@dataclass
class _Table:
    rows: list[list[Cell]] = field(default_factory=list)
    row: list[Cell] | None = None
    chunks: list[str] | None = None
    links: list[str] = field(default_factory=list)
    colspan: int = 1

    def finish_cell(self) -> None:
        if self.chunks is not None and self.row is not None:
            self.row.append(Cell("".join(self.chunks).strip(), self.links))
            self.row.extend(Cell() for _ in range(self.colspan - 1))
        self.chunks = None
        self.links = []

    def finish_row(self) -> None:
        self.finish_cell()
        if self.row is not None:
            self.rows.append(self.row)
        self.row = None


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self.stack: list[_Table] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "table":
            table = _Table()
            self.tables.append(table)
            self.stack.append(table)
            return
        if not self.stack:
            return
        table = self.stack[-1]
        attrs_dict = dict(attrs)
        if tag == "tr":
            table.finish_row()
            table.row = []
        elif tag in {"td", "th"}:
            table.finish_cell()
            if table.row is None:
                table.row = []
            table.chunks = []
            try:
                table.colspan = max(1, min(1000, int(attrs_dict.get("colspan") or "1")))
            except ValueError:
                table.colspan = 1
        elif table.chunks is not None:
            if tag == "a" and attrs_dict.get("href"):
                table.links.append(attrs_dict["href"])
            elif tag in {"br", "p", "div", "li"}:
                table.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored or not self.stack:
            return
        table = self.stack[-1]
        if tag in {"td", "th"}:
            table.finish_cell()
        elif tag == "tr":
            table.finish_row()
        elif tag == "table":
            table.finish_row()
            self.stack.pop()
        elif tag in {"p", "div", "li"} and table.chunks is not None:
            table.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored and self.stack and self.stack[-1].chunks is not None:
            self.stack[-1].chunks.append(data)


def read_html_excel(data: bytes, selected: str | None = None) -> list[Sheet]:
    # HTML exports do not have Excel worksheet names.
    if selected is not None and selected != "HTML":
        raise CollectionError(f"HTML 형식의 엑셀 파일에는 'HTML' 시트만 있습니다: {selected}")
    charset = re.search(rb"charset\s*=\s*['\"]?([\w-]+)", data[:8192], re.IGNORECASE)
    encodings = [charset.group(1).decode("ascii")] if charset else []
    encodings.extend(["utf-8-sig", "cp949"])
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    text = None
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except (UnicodeError, LookupError):
            continue
    if text is None:
        raise CollectionError("HTML 엑셀의 문자 인코딩을 해석하지 못했습니다.")
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    for table in parser.tables:
        table.finish_row()
    if not parser.tables:
        raise CollectionError("HTML 파일에서 표를 찾지 못했습니다.")
    return [Sheet("HTML", [row for table in parser.tables for row in table.rows])]
