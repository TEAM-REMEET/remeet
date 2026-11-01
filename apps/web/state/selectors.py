"""파생 데이터 계산 (읽기 전용).

AppState + 목업 데이터를 조합해 화면이 바로 쓸 수 있는 값을 만듭니다.
여기서는 상태를 바꾸지 않습니다. 바꾸는 일은 state/actions.py 담당입니다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from config import CURRENT_USER_ID as ME
from config import YEAR
from data import repository as repo
from data.models import Folder, Meeting, Person
from state.store import AppState
from utils.dates import date_key

TYPE_ORDER = ("weekly", "monthly", "normal")


# ---------------------------------------------------------------- 사람
def person(pid: str) -> Person:
    return repo.people()[pid]


def name_of(pid: str) -> str:
    return person(pid).name


def speaker_id(short_name: str) -> str | None:
    return repo.people_by_short_name().get(short_name)


def me() -> Person:
    return person(ME)


# ---------------------------------------------------------------- 폴더
def folders(state: AppState) -> dict[str, Folder]:
    """지금 존재하는 폴더 (새로 만든 폴더 포함, 삭제한 폴더 제외)."""
    out = dict(repo.base_folders())
    for nf in state.new_folders:
        out[nf.id] = Folder(nf.id, nf.name, nf.type, ME, (ME,), (), is_new=True)
    if state.branched and "rel" in out:
        notes = list(out["rel"].notes)
        notes.insert(1, "m9")
        out["rel"] = replace(out["rel"], notes=tuple(notes))
    for fid in state.deleted_folders:
        out.pop(fid, None)
    return out


def members_of(state: AppState, fid: str) -> list[str]:
    f = folders(state)[fid]
    members = list(f.members) + state.members_added.get(fid, [])
    return [p for p in members if f"{fid}:{p}" not in state.members_removed]


def folder_ids_of_type(state: AppState, mtype: str) -> list[str]:
    """내가 멤버인 폴더만 (회의 종류별)."""
    return [k for k, f in folders(state).items() if f.type == mtype and ME in members_of(state, k)]


def all_my_folder_ids(state: AppState) -> list[str]:
    return [k for t in TYPE_ORDER for k in folder_ids_of_type(state, t)]


def visible_note_ids(state: AppState) -> list[str]:
    ids: list[str] = []
    for k, f in folders(state).items():
        if ME in members_of(state, k):
            ids.extend(f.notes)
    return ids


def folder_label(state: AppState, m: Meeting) -> str:
    fs = folders(state)
    if m.topic in fs:
        f = fs[m.topic]
        return f"{repo.meeting_types()[f.type].name} / {f.name}"
    return "공유 받은 회의" if m.shared else ""


# ---------------------------------------------------------------- 회의 / 역할
def by_date_desc(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda i: date_key(repo.meeting(i).date), reverse=True)


def my_role(m: Meeting) -> str:
    """host(주관) | att(참석) | shared(공유받음) | member(미참석 폴더 멤버)"""
    if m.shared:
        return "shared"
    if m.host == ME:
        return "host"
    return "att" if ME in m.people else "member"


ROLE_TAGS = {
    "host": ("주관", "host"),
    "att": ("참석", "att"),
    "shared": ("공유받음", "shared"),
    "member": ("미참석", "abs"),
}


def mine_count(mid: str) -> int:
    return sum(1 for t in repo.meeting(mid).todos if t.owner == ME)


def todo_key(mid: str, index: int) -> str:
    return f"{mid}:{index}"


def unassigned_count(state: AppState, mid: str) -> int:
    m = repo.meeting(mid)
    return sum(1 for i, t in enumerate(m.todos) if not t.owner and todo_key(mid, i) not in state.assigned)


def status_of(state: AppState, mid: str) -> str:
    """review | confirmed | sending | fail | done"""
    return state.status_override.get(mid) or repo.meeting(mid).initial_status


def scope_label(scope: str) -> str:
    return "요약·결정만" if scope == "summary" else "원문 포함"


def scope_tone(scope: str) -> str:
    return "shared" if scope == "summary" else "host"


# ---------------------------------------------------------------- 표 한 줄 (홈 · 폴더 공통)
@dataclass(frozen=True)
class MeetingRow:
    id: str
    title: str
    sub: str
    is_deep: bool
    date_str: str
    time: str
    host: str
    is_me: bool
    count: int
    status: str
    status_tone: str
    status_sub: str
    actions: int
    action_sub: str
    button: str
    button_kind: str  # view | review | deliver | retry | progress


_STATUS_VIEW = {
    "review": ("검토 대기", "warn"),
    "confirmed": ("확정 · 전달 전", "ok"),
    "sending": ("전달 중", "host"),
    "fail": ("전송 실패", "fail"),
    "done": ("전달 완료", "hold"),
}


def meeting_row(state: AppState, mid: str) -> MeetingRow:
    m = repo.meeting(mid)
    st = status_of(state, mid)
    is_host = m.host == ME
    month, day = m.date.split("/")
    dotted = m.date.replace("/", ".")
    sub_by_status = {
        "review": "확정 전 검토가 필요해요",
        "confirmed": f"{dotted} 확정 · 버전 1",
        "sending": "참석자에게 보내는 중",
        "fail": "일부 참석자 미전달",
        "done": f"{dotted} 전달",
    }
    button, kind = "결과 보기", "view"
    if is_host:
        button, kind = {
            "review": ("검토하기", "review"),
            "confirmed": ("전달하기", "deliver"),
            "fail": ("재시도", "retry"),
            "sending": ("진행 보기", "progress"),
        }.get(st, (button, kind))
    mine = mine_count(mid)
    label, tone = _STATUS_VIEW[st]
    return MeetingRow(
        id=mid,
        title=m.title,
        sub=f"{folder_label(state, m)} · {m.file}",
        is_deep=m.deep,
        date_str=f"{YEAR}.{int(month):02d}.{int(day):02d} ({m.weekday})",
        time=m.time or "-",
        host=name_of(m.host),
        is_me=is_host,
        count=len(m.people),
        status=label,
        status_tone=tone,
        status_sub=sub_by_status[st],
        actions=len(m.todos),
        action_sub=(f"내 할 일 {mine} · " if mine else "") + f"담당자 미정 {unassigned_count(state, mid)}",
        button=button,
        button_kind=kind,
    )


# ---------------------------------------------------------------- 홈
def home_meeting_ids(state: AppState) -> list[str]:
    """내가 주관하거나 참석한 회의 (최근 순)."""
    return by_date_desc([i for i in visible_note_ids(state) if ME in repo.meeting(i).people])


# ---------------------------------------------------------------- 상단 경로
def breadcrumbs(state: AppState) -> list[str]:
    s = state.screen
    types = repo.meeting_types()
    fs = folders(state)
    if s == "wiki":
        return ["내 위키"]
    if s == "sent":
        return ["공유", "공유한 회의"]
    if s == "recv":
        return ["공유", "공유 받은 회의"]
    if s == "mcp":
        return ["설정", "외부 연동"]
    if s == "folder":
        crumbs = ["회의", types[state.folder_type].name]
        if state.folder_id in fs:
            crumbs.append(fs[state.folder_id].name)
        return crumbs
    if s == "note":
        m = repo.meeting(state.meeting_id)
        if m.topic in fs:
            f = fs[m.topic]
            return ["회의", types[f.type].name, f.name, m.title]
        return ["공유 받은 회의", m.title]
    if s == "deliver":
        return ["회의", repo.meeting(state.delivery.meeting_id).title, "결과 전달"]
    return ["내 회의"]


def has_integration_warning(state: AppState) -> bool:
    return any(v == "expired" for v in state.integrations.values())


def source_label(mid: str, src: tuple[str, ...]) -> str:
    """위키 출처 라벨: '10/27 주간 회의 · 이서윤'."""
    m = repo.meeting(mid)
    para = m.paragraph(src[0]) if src else None
    who = ""
    if para:
        pid = speaker_id(para.speaker)
        who = name_of(pid) if pid else para.speaker
    return f"{m.date} {m.title.split(' — ')[0]} · {who}"


def unread_notifications(state: AppState) -> int:
    return sum(1 for n in repo.notifications() if n.unread and n.id not in state.read_notifications)



def is_unread(state: AppState, nid: str) -> bool:
    n = next(x for x in repo.notifications() if x.id == nid)
    return n.unread and nid not in state.read_notifications


def show_bell_dot(state: AppState) -> bool:
    return any(is_unread(state, n.id) and n.id not in state.bell_seen for n in repo.notifications())
