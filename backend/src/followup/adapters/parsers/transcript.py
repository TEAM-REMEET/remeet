"""Read text and document paragraphs without extracting archives to disk."""
import re
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET


def read_transcript(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        text = path.read_text(encoding="utf-8-sig")
    elif suffix in {".docx", ".hwpx"}:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(e.file_size for e in entries) > 50_000_000:
                raise ValueError("Document archive is too large")
            if suffix == ".docx":
                names = ["word/document.xml"]
                ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            else:
                names = sorted(
                    (e.filename for e in entries
                     if re.fullmatch(r"Contents/section\d+\.xml", e.filename)),
                    key=lambda name: int(re.search(r"section(\d+)", name)[1]),
                )
                ns = "http://www.hancom.co.kr/hwpml/2011/paragraph"
            paragraphs = []
            for name in names:
                raw = archive.read(name)
                if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise ValueError("XML declarations are not supported")
                root = ET.fromstring(raw)
                for p in root.iter(f"{{{ns}}}p"):
                    # Nested table paragraphs are collected independently, without duplication.
                    parts = []
                    def collect(node):
                        for child in node:
                            if child.tag == f"{{{ns}}}p":
                                continue
                            if child.tag == f"{{{ns}}}t":
                                parts.append(child.text or "")
                            elif child.tag in {f"{{{ns}}}tab", f"{{{ns}}}br", f"{{{ns}}}lineBreak"}:
                                parts.append(" ")
                            else:
                                collect(child)
                    collect(p)
                    if parts:
                        paragraphs.append("".join(parts))
            text = "\n".join(paragraphs)
    else:
        raise ValueError("Supported inputs: .txt, .docx, .hwpx")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("Transcript is empty")
    return text
