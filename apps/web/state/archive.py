"""회의록 보관함: 회의 종류 → 보기 방식(기간별 / 프로젝트별) → 기간 묶음.

  회의록
  ├ 주간 회의 ─ 주차별 ─ 10월 4주차 ─ 회의 파일
  │           └ 프로젝트별 ─ 폴더 ─ 회의 파일
  ├ 월간 회의 ─ 월별 / 프로젝트별
  └ 일반 회의 ─ 일별 / 프로젝트별

사이드바 트리와 「회의록」 페이지가 같은 계산을 씁니다. 상태는 바꾸지 않습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from state.store import me_id
from config import YEAR
from data import repository as repo
from state import selectors as sel
from state.store import AppState

GROUPS = ("period", "project")

# 회의 종류별 '기간' 보기 이름
PERIOD_LABEL = {"weekly": "주차별", "monthly": "월별", "normal": "일별"}
PERIOD_UNIT = {"weekly": "주", "monthly": "달", "normal": "날"}


def group_label(mtype: str, group: str) -> str:
    return PERIOD_LABEL[mtype] if group == "period" else "프로젝트별"


def _day(md: str) -> date:
    month, day = md.split("/")
    return date(YEAR, int(month), int(day))


def period_key(mtype: str, md: str) -> str:
    """회의 날짜 → 기간 키. 주간: w1027(그 주 월요일) · 월간: m10 · 일반: d1023"""
    d = _day(md)
    if mtype == "weekly":
        monday = d - timedelta(days=d.weekday())
        return f"w{monday.month:02d}{monday.day:02d}"
    if mtype == "monthly":
        return f"m{d.month:02d}"
    return f"d{d.month:02d}{d.day:02d}"


def period_label(key: str) -> str:
    """기간 키 → 화면 이름. w1026 → 10월 4주차 · m10 → 10월 · d1023 → 10월 23일"""
    kind, rest = key[0], key[1:]
    if kind == "m":
        return f"{int(rest)}월"
    d = date(YEAR, int(rest[:2]), int(rest[2:]))
    if kind == "w":
        return f"{d.month}월 {(d.day - 1) // 7 + 1}주차"
    return f"{d.month}월 {d.day}일"


def meeting_ids_of_type(state: AppState, mtype: str, owner: str = "all") -> list[str]:
    """내가 멤버인 폴더 중 이 회의 종류의 회의록 (최근 순). 미참석 회의는 공유받은 것만.

    owner: all | mine(내가 만든 폴더) | member(멤버로 들어간 폴더)
    """
    ids: list[str] = []
    for fid in sel.folder_ids_by_owner(state, mtype, owner) + _period_containers(state, mtype, owner):
        ids.extend(sel.folder_note_ids(state, fid))
    return sel.by_date_desc(list(dict.fromkeys(ids)))


def _period_containers(state: AppState, mtype: str, owner: str = "all") -> list[str]:
    fs = sel.folders(state)
    fids = sel.period_container_ids(state, mtype)
    if owner == "mine":
        return [k for k in fids if fs[k].host == me_id()]
    if owner == "member":
        return [k for k in fids if fs[k].host != me_id()]
    return fids


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    meeting_ids: tuple[str, ...]


def periods(state: AppState, mtype: str, owner: str = "all") -> list[Period]:
    """이 회의 종류의 기간 묶음 (최근 기간 먼저, 안의 회의도 최근 순).

    주차별 · 월별 · 일별로 저장한 회의만 들어가요. 프로젝트별 폴더의 회의와는 서로 겹치지 않아요.
    """
    buckets: dict[str, list[str]] = {}
    ids = [i for k in _period_containers(state, mtype, owner) for i in sel.folder_note_ids(state, k)]
    for mid in sel.by_date_desc(list(dict.fromkeys(ids))):
        buckets.setdefault(period_key(mtype, repo.meeting(mid).date), []).append(mid)
    return [Period(k, period_label(k), tuple(v)) for k, v in sorted(buckets.items(), key=lambda kv: kv[0], reverse=True)]


def period(state: AppState, mtype: str, key: str, owner: str = "all") -> Period | None:
    return next((p for p in periods(state, mtype, owner) if p.key == key), None)


def period_folder_ids(state: AppState, p: Period) -> list[str]:
    """기간 폴더 안 회의들이 속한 프로젝트 폴더 (최근 회의 순)."""
    return list(dict.fromkeys(repo.meeting(i).topic for i in p.meeting_ids))


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
# root · t:weekly · g:weekly:period · p:weekly:w1027 · g:weekly:project · f:weekly:rel
def node_of_location(state: AppState) -> tuple[str, list[str]]:
    """지금 화면에 해당하는 트리 노드와 그 조상들 (사이드바 강조·자동 펼침용)."""
    if state.screen == "archive":
        t, g, p = state.archive_type, state.archive_group, state.archive_period
        chain = ["root"]
        if t:
            chain.append(f"t:{t}")
        if t and g:
            chain.append(f"g:{t}:{g}")
        if t and g == "period" and p:
            chain.append(f"p:{t}:{p}")
        return chain[-1], chain[:-1]
    if state.screen == "note" and sel.is_period_folder(repo.meeting(state.meeting_id).topic or ""):
        m = repo.meeting(state.meeting_id)
        t = sel.folders(state)[m.topic].type if m.topic in sel.folders(state) else ""
        if t:
            node = f"p:{t}:{period_key(t, m.date)}"
            return node, ["root", f"t:{t}", f"g:{t}:period", node]
    if state.screen in ("folder", "note"):
        fid = state.folder_id if state.screen == "folder" else repo.meeting(state.meeting_id).topic
        fs = sel.folders(state)
        if fid in fs:
            t = fs[fid].type
            node = f"f:{t}:{fid}"
            return node, ["root", f"t:{t}", f"g:{t}:project"] + ([node] if state.screen == "note" else [])
    return "", []
