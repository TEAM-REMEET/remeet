"""첨부 파일(회의 내용) 읽기: TXT · MD · DOCX · HWPX.

- TXT · MD: 글자 그대로 (UTF-8, 안 되면 CP949 · TXT · MD는 올리면 회의 내용 입력칸에 바로 채움)
- DOCX(워드) · HWPX(한글): 압축 파일 안의 XML 에서 문단 글자만 꺼냄 (문단마다 한 줄, 빈 문단은 빈 줄)
그 밖의 형식은 읽지 않아요. 「발화자 확인」 때 안내하고 다음 단계로 못 가요 (state/actions.compose_next).
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

ALLOWED = ("txt", "md", "docx", "hwpx")
ALLOWED_LABEL = "TXT · MD · DOCX · HWPX"
INLINE_EXTS = ("txt", "md")  # 올리면 내용을 「회의 내용」 입력칸에 바로 채우는 형식 (나머지는 첨부 파일로 둠)


def ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_allowed(name: str) -> bool:
    return ext(name) in ALLOWED


def _paragraphs(xml: bytes) -> list[str]:
    """XML 안의 문단(p) → 글자(t)를 이어 붙인 줄들. (워드 w:p/w:t · 한글 hp:p/hp:t 모두 이름 끝이 p · t)"""
    root = ET.fromstring(xml)
    lines = []
    for p in root.iter():
        if not p.tag.endswith("}p"):
            continue
        if any(a.tag.endswith("}p") for a in p.iter() if a is not p):  # 표 안 문단처럼 문단을 품은 문단은 건너뜀
            continue
        lines.append("".join(t.text or "" for t in p.iter() if t.tag.endswith("}t")))
    return lines


def _decode(data: bytes) -> str:
    """글자 파일 → 글. UTF-8(BOM 포함)로 읽고, 안 되면 Windows 메모장의 ANSI(CP949)로 읽음."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_text(name: str, data: bytes) -> str | None:
    """파일 내용 → 글. 지원하지 않는 형식이거나 읽을 수 없으면 None."""
    kind = ext(name)
    try:
        if kind in ("txt", "md"):
            return _decode(data)
        if kind == "docx":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "\n".join(_paragraphs(z.read("word/document.xml")))
        if kind == "hwpx":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                sections = sorted((n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                                  key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[-1]).group()))
                return "\n".join(line for s in sections for line in _paragraphs(z.read(s)))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError):
        return None
    return None
