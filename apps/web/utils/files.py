"""첨부 파일(회의 내용) 읽기: TXT · MD · DOCX · HWP · HWPX.

- TXT · MD: 글자 그대로 (UTF-8, 안 되면 CP949)
- DOCX(워드) · HWPX(한글): 압축 파일 안의 XML 에서 문단 글자만 꺼냄 (문단마다 한 줄, 빈 문단은 빈 줄, 문단 안 줄바꿈 · 탭 유지)
- HWP(한글 예전 형식 · 바이너리): utils/hwp.py 가 본문 문단을 꺼냄 (같은 규칙 · 암호 · 배포용 문서는 못 읽음)
지원하는 모든 형식은 올리면 회의 내용 입력칸에 바로 채움 (INLINE_EXTS).
그 밖의 형식은 읽지 않아요. 「발화자 확인」 때 안내하고 다음 단계로 못 가요 (state/actions.compose_next).
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from utils.hwp import read_hwp

ALLOWED = ("txt", "md", "docx", "hwp", "hwpx")
ALLOWED_LABEL = "TXT · MD · DOCX · HWPX"  # 화면에 보이는 형식 (HWP도 읽지만 화면에는 적지 않음)
INLINE_EXTS = ("txt", "md", "docx", "hwp", "hwpx")  # 올리면 내용을 「회의 내용」 입력칸에 바로 채우는 형식 (나머지는 첨부 파일로 둠)


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
        lines.append(_text(p))
    return lines


def _text(p: ET.Element) -> str:
    """문단 하나의 글자. 문단 안 줄바꿈(워드 Shift+Enter w:br · 한글 hp:lineBreak)은 줄바꿈, 탭은 탭으로.

    문단 설정(w:pPr) 안의 탭 위치 정의(w:tabs/w:tab)는 글자가 아니라서 건너뜀.
    """
    out: list[str] = []

    def walk(e: ET.Element, in_text: bool) -> None:
        name = e.tag.rsplit("}", 1)[-1]
        if name == "pPr":
            return
        if name in ("br", "lineBreak"):
            out.append("\n")
        elif name == "tab":
            out.append("\t")
        elif name == "t":
            out.append(e.text or "")
            in_text = True
        for child in e:
            walk(child, in_text)
            if in_text and child.tail:  # 한글: <hp:t>앞<hp:lineBreak/>뒤</hp:t> 의 「뒤」
                out.append(child.tail)

    walk(p, False)
    return "".join(out)


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
        if kind == "hwp":
            return read_hwp(data)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError):  # HwpError도 ValueError
        return None
    return None
