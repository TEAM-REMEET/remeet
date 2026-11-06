"""문서 파일 읽기 (.txt · .docx · .hwpx)"""
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from followup.adapters.parsers.transcript import read_transcript


class ReadTranscriptTests(unittest.TestCase):
    def test_txt_handles_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.txt"
            path.write_bytes("﻿첫 줄\r\n둘째 줄".encode("utf-8"))
            self.assertEqual(read_transcript(path), "첫 줄\n둘째 줄")

    def test_document_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix, member, ns in [
                ("docx", "word/document.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
                ("hwpx", "Contents/section0.xml", "http://www.hancom.co.kr/hwpml/2011/paragraph"),
            ]:
                path = Path(folder) / f"meeting.{suffix}"
                with ZipFile(path, "w") as archive:
                    archive.writestr(member, f'<root xmlns:p="{ns}"><p:p><p:r><p:t>회의 내용</p:t></p:r></p:p></root>')
                self.assertEqual(read_transcript(path), "회의 내용")
            with ZipFile(path, "w") as archive:
                archive.writestr(member, '<!DOCTYPE root><root/>')
            with self.assertRaises(ValueError):
                read_transcript(path)

    def test_unsupported_and_empty_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, content in (("m.pdf", "x"), ("m.txt", "  ")):
                path = Path(folder) / name
                path.write_text(content, encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(ValueError):
                    read_transcript(path)


if __name__ == "__main__":
    unittest.main()
