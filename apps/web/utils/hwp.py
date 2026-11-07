"""HWP(한글 97~2022의 .hwp, HWP 5.0) 파일 → 글. 외부 라이브러리 없이 파이썬 기본 기능만 사용.

HWP 5.0 구조 (한컴 공개 문서 「한글 문서 파일 형식 5.0」):
- 파일 전체는 MS 복합 문서(CFB · OLE) 형식: 안에 여러 「스트림」이 폴더처럼 들어 있음
- FileHeader 스트림: 압축 여부 · 암호 · 배포용 문서 여부
- BodyText/Section0, Section1 … 스트림: 본문 (보통 zlib 압축) → 「레코드」가 이어 붙어 있음
  - 문단 머리(PARA_HEADER)마다 한 줄, 그 뒤 문단 글자(PARA_TEXT)는 UTF-16 글자 + 제어 문자
- 머리말 · 꼬리말 · 각주 · 미주 · 숨은 설명은 본문이 아니라서 건너뜀

암호가 걸렸거나 배포용 문서(본문이 암호화됨)는 읽을 수 없어 None.
"""

from __future__ import annotations

import re
import struct
import zlib

_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_END = 0xFFFFFFFE  # 체인의 끝
_NOSTREAM = 0xFFFFFFFF

_PARA_HEADER, _PARA_TEXT, _CTRL_HEADER = 0x10 + 50, 0x10 + 51, 0x10 + 55
_SKIP_CTRLS = {b"head", b"foot", b"fn  ", b"en  ", b"tcmt"}  # 머리말 · 꼬리말 · 각주 · 미주 · 숨은 설명
_ONE_UNIT = {0, 10, 13, *range(24, 32)}  # 글자 한 칸짜리 제어 문자 (나머지 제어 문자는 8칸)
_SPECIAL = {10: "\n", 24: "-", 30: " ", 31: " "}  # 줄바꿈 · 하이픈 · 묶음 빈칸 · 고정폭 빈칸


class HwpError(ValueError):
    """HWP로 읽을 수 없음 (형식이 다름 · 손상 · 암호 · 배포용)."""


# ---------------------------------------------------------------- 복합 문서(CFB) 읽기
class _Cfb:
    def __init__(self, data: bytes):
        if data[:8] != _SIGNATURE:
            raise HwpError("복합 문서가 아님")
        self.data = data
        self.ss = 1 << struct.unpack_from("<H", data, 0x1E)[0]  # 섹터 크기 (512 · 4096)
        self.mss = 1 << struct.unpack_from("<H", data, 0x20)[0]  # 작은 섹터 크기 (64)
        n_fat, first_dir = struct.unpack_from("<II", data, 0x2C)
        self.cutoff, first_mfat, n_mfat, first_difat, n_difat = struct.unpack_from("<IIIII", data, 0x38)
        difat = [x for x in struct.unpack_from("<109I", data, 0x4C)]
        sect = first_difat
        for _ in range(n_difat):  # 109개를 넘는 FAT 위치는 DIFAT 섹터에 이어서
            if sect >= _END:
                break
            vals = struct.unpack_from(f"<{self.ss // 4}I", data, self._off(sect))
            difat += vals[:-1]
            sect = vals[-1]
        self.fat: list[int] = []
        for s in difat[:n_fat]:
            self.fat += struct.unpack_from(f"<{self.ss // 4}I", data, self._off(s))
        self.minifat: list[int] = []
        if n_mfat and first_mfat < _END:
            raw = self._chain(first_mfat)
            self.minifat = list(struct.unpack_from(f"<{len(raw) // 4}I", raw))
        dirs = self._chain(first_dir)
        self.entries = [self._entry(dirs[i:i + 128]) for i in range(0, len(dirs) - 127, 128)]
        root = self.entries[0]
        self.ministream = self._chain(root["start"])[:root["size"]] if root["start"] < _END else b""

    def _off(self, sect: int) -> int:
        return (sect + 1) * self.ss

    def _chain(self, sect: int) -> bytes:
        out, seen = bytearray(), set()
        while sect < _END:
            if sect in seen or sect >= len(self.fat):
                raise HwpError("섹터 체인이 손상됨")
            seen.add(sect)
            out += self.data[self._off(sect):self._off(sect) + self.ss]
            sect = self.fat[sect]
        return bytes(out)

    def _mini_chain(self, sect: int, size: int) -> bytes:
        out, seen = bytearray(), set()
        while sect < _END and len(out) < size:
            if sect in seen or sect >= len(self.minifat):
                raise HwpError("작은 섹터 체인이 손상됨")
            seen.add(sect)
            out += self.ministream[sect * self.mss:(sect + 1) * self.mss]
            sect = self.minifat[sect]
        return bytes(out)

    @staticmethod
    def _entry(raw: bytes) -> dict:
        name_len = struct.unpack_from("<H", raw, 0x40)[0]
        left, right, child = struct.unpack_from("<III", raw, 0x44)
        start, size = struct.unpack_from("<II", raw, 0x74)
        return {"name": raw[:max(name_len - 2, 0)].decode("utf-16-le", errors="replace"), "type": raw[0x42],
                "left": left, "right": right, "child": child, "start": start, "size": size}

    def children(self, index: int) -> dict[str, int]:
        """폴더(저장소) 안의 이름 → 항목 번호 (형제는 이진 트리로 이어져 있음)."""
        out, todo, seen = {}, [self.entries[index]["child"]], set()
        while todo:
            i = todo.pop()
            if i == _NOSTREAM or i in seen or i >= len(self.entries):
                continue
            seen.add(i)
            e = self.entries[i]
            out[e["name"]] = i
            todo += [e["left"], e["right"]]
        return out

    def read(self, index: int) -> bytes:
        e = self.entries[index]
        if e["size"] < self.cutoff:
            return self._mini_chain(e["start"], e["size"])[:e["size"]]
        return self._chain(e["start"])[:e["size"]]


# ---------------------------------------------------------------- HWP 본문
def _records(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        head = struct.unpack_from("<I", data, pos)[0]
        tag, level, size = head & 0x3FF, (head >> 10) & 0x3FF, head >> 20
        pos += 4
        if size == 0xFFF:  # 큰 레코드는 크기를 다음 4바이트에
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        yield tag, level, data[pos:pos + size]
        pos += size


def _para_text(raw: bytes) -> str:
    units = struct.unpack_from(f"<{len(raw) // 2}H", raw)
    out, buf, i = [], bytearray(), 0
    while i < len(units):
        c = units[i]
        if c >= 32:
            buf += struct.pack("<H", c)
            i += 1
            continue
        if buf:  # 모아 둔 글자 (한글 · 이모지 등 UTF-16) 풀기
            out.append(buf.decode("utf-16-le", errors="replace"))
            buf = bytearray()
        if c == 9:  # 탭 (8칸짜리 제어 문자)
            out.append("\t")
        else:
            out.append(_SPECIAL.get(c, ""))  # 13(문단 끝) 등은 글자 없음
        i += 1 if c in _ONE_UNIT else 8  # 표 · 그림 · 머리말 등 8칸짜리 제어 문자는 통째로 건너뜀
    if buf:
        out.append(buf.decode("utf-16-le", errors="replace"))
    return "".join(out)


def _section_lines(data: bytes) -> list[str]:
    lines: list[str] = []
    skip_above: int | None = None  # 머리말 · 각주 등 안쪽(이 단계보다 깊은 레코드)은 건너뜀
    for tag, level, body in _records(data):
        if skip_above is not None:
            if level > skip_above:
                continue
            skip_above = None
        if tag == _CTRL_HEADER and len(body) >= 4 and struct.pack(">I", struct.unpack_from("<I", body)[0]) in _SKIP_CTRLS:
            skip_above = level
        elif tag == _PARA_HEADER:
            lines.append("")
        elif tag == _PARA_TEXT and lines:
            lines[-1] += _para_text(body)
    return lines


def read_hwp(data: bytes) -> str:
    """HWP 파일 내용 → 글 (문단마다 한 줄, 빈 문단은 빈 줄, 문단 안 줄바꿈 · 탭 유지). 못 읽으면 HwpError."""
    try:
        cfb = _Cfb(data)
        root = cfb.children(0)
        if "FileHeader" not in root or "BodyText" not in root:
            raise HwpError("HWP 문서가 아님 (배포용 문서일 수 있음)")
        header = cfb.read(root["FileHeader"])
        if not header.startswith(b"HWP Document File"):
            raise HwpError("HWP 문서가 아님")
        flags = struct.unpack_from("<I", header, 36)[0]
        if flags & 0b110:  # 암호 · 배포용
            raise HwpError("암호가 걸렸거나 배포용 문서")
        sections = cfb.children(root["BodyText"])
        names = sorted((n for n in sections if re.fullmatch(r"Section\d+", n)), key=lambda n: int(n[7:]))
        lines = []
        for name in names:
            raw = cfb.read(sections[name])
            lines += _section_lines(zlib.decompress(raw, -15) if flags & 1 else raw)
        return "\n".join(lines)
    except (struct.error, zlib.error, IndexError, KeyError, UnicodeDecodeError) as e:
        raise HwpError(str(e)) from e
