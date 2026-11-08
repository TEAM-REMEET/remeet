"""회의록 보관함 계산 (상태는 바꾸지 않습니다).

구조: 프로젝트 ─ 회의. 회의마다 종류(directory_id: 월간 · 주간 · 일반)가 있어요.

사이드바 트리 (토글로 전환)
  정기 회의: 월간 회의 ─ 2026년 ─ 9월 (2026.09.26)
            주간 회의 ─ 2026년 ─ 10월 ─ 4주차 (2026.10.27)
            일반 회의 ─ 2026.10.23
  프로젝트: 프로젝트 ─ 11월 릴리즈 준비 ─ 2026.10.29
「회의록」 페이지: 월간 · 주간 · 일반 회의 카드 → 그 종류의 회의 목록
"""

from __future__ import annotations

from datetime import date, timedelta

from state.store import me_id
from config import YEAR
from data import repository as repo
from state import selectors as sel
from state.store import AppState


def _day(md: str) -> date:
    month, day = md.split("/")
    return date(YEAR, int(month), int(day))


def meeting_day(mid: str) -> date:
    return _day(repo.meeting(mid).date)


def dotted(d: date) -> str:
    """26.10.27 (사이드바 회의 파일 이름)"""
    return f"{d:%y.%m.%d}"


def week_of(d: date) -> tuple[date, int]:
    """그 주 월요일과 몇 주차인지. 주차는 월요일이 속한 달 기준 (10월 27일 월요일 → 10월 4주차)."""
    monday = d - timedelta(days=d.weekday())
    return monday, (monday.day - 1) // 7 + 1


def meeting_ids_of_type(state: AppState, mtype: str, owner: str = "all") -> list[str]:
    """내 프로젝트의 회의 중 이 종류(directory_id)의 회의록 (최근 순). 미참석 회의는 공유받은 것만.

    owner: all | mine(내가 만든 프로젝트) | member(멤버로 들어간 프로젝트)
    """
    ids = [i for fid in sel.folder_ids_by_owner(state, owner) for i in sel.folder_note_ids(state, fid)]
    return sel.by_date_desc([i for i in dict.fromkeys(ids) if sel.meeting_type(state, i) == mtype])


def by_role(ids: list[str] | tuple[str, ...], role: str) -> list[str]:
    """전체 / 내가 주관 / 참석 필터."""
    if role == "host":
        return [i for i in ids if sel.my_role(repo.meeting(i)) == "host"]
    if role == "att":
        return [i for i in ids if sel.my_role(repo.meeting(i)) == "att"]
    return list(ids)


def my_count(ids: list[str] | tuple[str, ...]) -> int:
    return sum(1 for i in ids if me_id() in repo.meeting(i).people)


# ---------------------------------------------------------------- 트리 노드 id
# 정기 회의:   root · t:monthly · y:monthly:2026 · ym:weekly:2026:10 · m:m1
# 프로젝트: root · proj · pf:rel · m:m1
def time_chain(mtype: str, d: date) -> list[str]:
    """정기 회의 트리에서 그 날짜의 회의를 담는 폴더 노드들 (위에서 아래로)."""
    if mtype == "normal":
        return ["root", "t:normal"]
    if mtype == "monthly":
        return ["root", "t:monthly", f"y:monthly:{d.year}"]
    monday, _ = week_of(d)
    return ["root", f"t:{mtype}", f"y:{mtype}:{monday.year}", f"ym:{mtype}:{monday.year}:{monday.month}"]


def node_of_location(state: AppState) -> tuple[str, list[str]]:
    """지금 화면에 해당하는 트리 노드와 그 조상들 (사이드바 강조·자동 펼침용)."""
    project = state.tree_mode == "project"
    if state.screen == "archive":
        t = state.archive_type
        if project or not t:
            return "root", []
        return f"t:{t}", ["root"]
    fs = sel.folders(state)
    if state.screen == "note":
        mid = state.meeting_id
        fid = repo.meeting(mid).project_id or ""
        if fid not in fs:
            return "", []
        if project:
            return f"m:{mid}", ["root", "proj", f"pf:{fid}"]
        return f"m:{mid}", time_chain(sel.meeting_type(state, mid), meeting_day(mid))
    if state.screen == "folder" and state.folder_id in fs:
        fid = state.folder_id
        return (f"pf:{fid}", ["root", "proj"]) if project else ("root", [])
    return "", []
