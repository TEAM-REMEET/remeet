"""파생 데이터 계산 (읽기 전용).

AppState + 목업 데이터를 조합해 화면이 바로 쓸 수 있는 값을 만듭니다.
여기서는 상태를 바꾸지 않습니다. 바꾸는 일은 state/actions.py 담당입니다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from state.store import me_id
from config import YEAR
from data import repository as repo
from data.models import MISC_AGENDA, Folder, Meeting, Person, Todo
from state.store import AppState
from utils.dates import date_key

TYPE_ORDER = ("weekly", "monthly", "normal")


# ---------------------------------------------------------------- 사람
def person(pid: str) -> Person:
    """없는 사람(예: 목업 회의가 가리키는 옛 목업 id)은 「알 수 없음」으로 보여 줘요."""
    return repo.people().get(pid) or Person(pid, "알 수 없음", "알 수 없음", "")


def name_of(pid: str) -> str:
    return person(pid).name


def assignee_name(who: str) -> str:
    """지정한 담당자(user_id) → 이름. 구성원에 없는 값(목업 후보 이름)은 그대로 보여 줘요."""
    p = repo.people().get(who)
    return p.name if p else who


def meeting_date(m: Meeting) -> date:
    """회의 날짜 (연도까지). 할 일 마감일은 이 날짜 이전으로 고를 수 없어요."""
    if m.held_on:
        return m.held_on
    month, day = m.date.split("/")
    return date(YEAR, int(month), int(day))


def agenda_options(m: Meeting) -> list[str]:
    """할 일을 넣을 수 있는 안건: 지금 안건들 + 맨 뒤에 기타 (이미 있으면 그대로)."""
    titles = [a.title for a in m.agendas if a.title != MISC_AGENDA]
    return [*titles, MISC_AGENDA]


def speaker_id(short_name: str) -> str | None:
    return repo.people_by_short_name().get(short_name)


def paragraph_speaker(p) -> str | None:
    """원문 한 줄의 화자 id. user_id가 있으면 그대로 써요 (다른 직무의 동명이인은 이름으로 구분되지 않음)."""
    return p.speaker_id or speaker_id(p.speaker)


def me() -> Person:
    return person(me_id())


def all_people_ids() -> list[str]:
    return list(repo.people())


def team_groups() -> list[tuple[str, list[str]]]:
    """[(팀 이름, [사람 id])] — 전체 사람을 팀별로 묶음 (내 팀 먼저 · 팀 안은 이름 가나다순). 주관자·참석자 고르기용."""
    groups: dict[str, list[str]] = {}
    for pid, p in sorted(repo.people().items(), key=lambda kv: kv[1].name):
        groups.setdefault(p.team, []).append(pid)
    mine = me().team
    return sorted(groups.items(), key=lambda kv: kv[0] != mine)


POSITION_LABEL = {"senior": "Senior", "mid": "Mid", "junior": "Junior"}  # 순서 = 담당자 추천 카드 순서


def person_desc(pid: str) -> str:
    """사람 목록 이름 옆: 직무 · 포지션 (없는 값은 뺌)."""
    pos = next((r.get("position") or "" for r in repo.people_rows() if r["user_id"] == pid), "")
    return " · ".join(x for x in (person_role(pid), POSITION_LABEL.get(pos, "")) if x)


def person_email(pid: str) -> str:
    """구성원 표의 이메일 (없으면 빈 값)."""
    return next((r.get("email") or "" for r in repo.people_rows() if r["user_id"] == pid), "")


def person_role(pid: str) -> str:
    """조직도에 있는 직무 (없으면 빈 값)."""
    for t in repo.teams().values():
        for mb in t.members:
            if mb.id == pid:
                return mb.role
    return ""


# ---------------------------------------------------------------- 폴더
def folders(state: AppState) -> dict[str, Folder]:
    """지금 존재하는 폴더 (새로 만든 폴더 포함, 삭제한 폴더 제외)."""
    out = dict(repo.base_folders())
    for nf in state.new_folders:
        out[nf.id] = Folder(nf.id, nf.name, me_id(), (me_id(),), (), is_new=True)
    if state.branched and "rel" in out:
        notes = list(out["rel"].notes)
        notes.insert(1, "m9")
        out["rel"] = replace(out["rel"], notes=tuple(notes))
    for fid, mid in state.created_notes:  # 「회의록 생성 완료」로 만든 회의를 그 폴더에 넣음 (최근 회의가 앞)
        if fid in out:
            out[fid] = replace(out[fid], notes=(mid, *out[fid].notes))
    for fid in state.deleted_folders:
        out.pop(fid, None)
    return out


def members_of(state: AppState, fid: str) -> list[str]:
    f = folders(state)[fid]
    members = list(f.members) + state.members_added.get(fid, [])
    return [p for p in members if f"{fid}:{p}" not in state.members_removed]


def meeting_type(state: AppState, mid: str) -> str:
    """회의 종류(weekly | monthly | normal) = 회의의 directory_id. 한 프로젝트에 여러 종류의 회의가 있어요."""
    return repo.meeting(mid).directory_id


def all_my_folder_ids(state: AppState) -> list[str]:
    """내가 멤버인 프로젝트(폴더)."""
    return [k for k in folders(state) if me_id() in members_of(state, k)]


def can_see(state: AppState, mid: str) -> bool:
    """회의록이 나에게 보이는지: 주관했거나 참석했거나, 미참석했지만 주관자가 공유해 준 경우."""
    if mid in state.deleted_meetings:
        return False
    if mid in state.new_meetings:  # 내가 새로 만든 회의록은 항상 보임
        return True
    m = repo.meeting(mid)
    return m.host == me_id() or me_id() in m.people or is_reviewer(m) or _shared_to_me(state, m)


def _shared_to_me(state: AppState, m: Meeting) -> bool:
    """백엔드 공유(GET /api/meetings/{id} 의 shared) 또는 이 세션에서 주관자가 보내 준 미참석 공유."""
    return m.shared is not None or me_id() in state.absent_shares.get(m.id, [])


def is_absent_shared(state: AppState, mid: str) -> bool:
    """미참석(조퇴·불참)했는데 주관자가 공유해 줘서 보이는 회의."""
    m = repo.meeting(mid)
    return m.host != me_id() and me_id() not in m.people and not is_reviewer(m) and _shared_to_me(state, m)


def folder_note_ids(state: AppState, fid: str) -> list[str]:
    """폴더 안에서 나에게 보이는 회의록 (최근 순). 프로젝트 · 회의는 따로 받아서(캐시 시점이 달라) 상세가 없는 회의는 뺌."""
    loaded = repo.meetings()
    return by_date_desc([i for i in folders(state)[fid].notes if i in loaded and can_see(state, i)])


def folder_ids_by_owner(state: AppState, owner: str) -> list[str]:
    """내 프로젝트를 전체 / 내가 만든 프로젝트(mine) / 멤버(member)로 거르기."""
    fids = all_my_folder_ids(state)
    if owner == "mine":
        return [k for k in fids if folders(state)[k].host == me_id()]
    if owner == "member":
        return [k for k in fids if folders(state)[k].host != me_id()]
    return fids


def visible_note_ids(state: AppState) -> list[str]:
    ids: list[str] = []
    for k in folders(state):
        if me_id() in members_of(state, k):
            ids.extend(folder_note_ids(state, k))
    # 검토를 맡은 회의록 (프로젝트 멤버가 아니어도 목록 · 검색에 나오게)
    ids.extend(mid for mid, m in repo.meetings().items() if is_reviewer(m) and mid not in ids)
    return ids


def folder_label(state: AppState, m: Meeting) -> str:
    fs = folders(state)
    if m.project_id in fs:  # 「주간 회의 / 11월 릴리즈 준비」
        return f"{repo.meeting_types()[m.directory_id].name} / {fs[m.project_id].name}"
    if is_reviewer(m):
        return "검토 요청 받은 회의"
    return "공유 받은 회의" if m.shared else ""


# ---------------------------------------------------------------- 회의 / 역할
def by_date_desc(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda i: date_key(repo.meeting(i).date), reverse=True)


def manages(m: Meeting) -> bool:
    """이 회의록을 고치고 확정 · 전달할 수 있는 사람 (DB 규칙과 같음): 주관자 · 검토자.
    신입 주관자와 윗사람(검토자) 둘 다 고치고 확정해요. 주관자가 아닌 사람이 쓴 회의록은 주관자가 맡아요."""
    return me_id() in (m.host, m.reviewer)


def owns(m: Meeting) -> bool:
    """회의 삭제 · 공유를 할 수 있는 사람 (DB 규칙과 같음): 주관자 · 작성자. 검토자는 검토(편집 · 확정 · 전달)만 해요.
    신입 주관자는 제출 뒤 고칠 수는 없어도 삭제 · 공유는 할 수 있어요."""
    return me_id() in (m.host, m.author)


def review_requested(state: AppState, m: Meeting) -> bool:
    """다른 사람이 써서 나에게 검토를 요청한 회의록 (내가 검토자 · 아직 확정 전)."""
    return m.id in state.review_requested or (
        m.reviewer == me_id() and m.author not in ("", me_id()) and m.initial_status == "review")


def position_of(pid: str) -> str:
    """직급 senior | mid | junior (구성원 GET /api/people). 모르면 빈 값."""
    return next((r.get("position") or "" for r in repo.people_rows() if r["user_id"] == pid), "")


def needs_reviewer(state: AppState) -> bool:
    """새 회의록: 내가 신입이고 내가 주관자 → 검토할 윗사람을 꼭 골라야 해요 (주관자가 다른 사람이면 주관자가 검토)."""
    return bool(state.nn_host) and state.nn_host == me_id() and position_of(me_id()) == "junior"


def reviewer_candidates(state: AppState) -> list[str]:
    """검토자 후보: 신입이 아닌 활성 구성원 (같은 팀 → 시니어 → 이름 순)."""
    rows = [r for r in repo.people_rows() if r["user_id"] != me_id() and r.get("is_active") is not False
            and (r.get("position") or "") in ("mid", "senior")]
    team = person(me_id()).team
    rows.sort(key=lambda r: ((r.get("team") or "") != team, r.get("position") != "senior", r["name"]))
    return [r["user_id"] for r in rows]


def sent_shares(state: AppState) -> list:
    """[공유한 회의] DB 에 저장된 공유 (GET /api/shares/sent) + 이 화면에서만 만든 공유."""
    from state.store import SentShare

    out = []
    names = {t["team_id"]: t["name"] for t in repo.catalog("teams")} if repo.sent_share_rows() else {}
    for r in repo.sent_share_rows():
        to = person(r["recipient_user_id"]).name if r.get("recipient_user_id") else names.get(r.get("recipient_team_id"), "팀")
        day = str(r.get("shared_on") or "")
        out.append(SentShare(r["meeting_id"], to, r["scope"],
                             f"{int(day[5:7])}/{int(day[8:10])}" if len(day) >= 10 else "", r["id"]))
    return out + list(state.sent_shares)


def is_reviewer(m: Meeting) -> bool:
    """내가 이 회의록의 검토자 (신입이 쓴 회의록의 윗사람 · 주관자 본인은 빼고)."""
    return bool(m.reviewer) and m.reviewer == me_id() and m.host != me_id()


def my_role(m: Meeting) -> str:
    """host(주관) | reviewer(검토자) | att(참석) | shared(공유받음) | member(미참석 폴더 멤버)"""
    if m.host == me_id():
        return "host"
    if is_reviewer(m):  # 참석자 · 프로젝트 멤버가 아니어도 「검토자」 (미참석 · 공유받음으로 보이지 않게)
        return "reviewer"
    if m.shared:
        return "shared"
    return "att" if me_id() in m.people else "member"


ROLE_TAGS = {
    "host": ("주관", "host"),
    "reviewer": ("검토자", "info"),
    "att": ("참석", "att"),
    "shared": ("공유받음", "shared"),
    "member": ("미참석", "abs"),
}


def deleted_todo_indexes(state: AppState, mid: str) -> list[int]:
    """삭제한 할 일 번호: 이 세션에서 지운 것(작성 중 미리보기) + DB 에서 지운 것 (Todo.deleted)."""
    session = list(state.deleted_todos.get(mid, []))
    return session + [i for i in repo.deleted_todo_indexes(mid) if i not in session]


def todo_items(state: AppState, mid: str) -> list[tuple[int, Todo]]:
    """이 회의의 액션 아이템 [(원래 순서 번호, 할 일)] · 사용자가 삭제한 것은 뺌.

    번호는 지우기 전 그대로라서, 번호로 저장한 완료 체크 · 담당자 지정 기록이 다른 할 일에 붙지 않아요.
    """
    gone = set(deleted_todo_indexes(state, mid))
    out = []
    for i, t in enumerate(repo.meeting(mid).todos):
        if i in gone:
            continue
        due = state.todo_due.get(todo_key(mid, i))
        out.append((i, replace(t, due=due, urgent=False) if due else t))  # 담당자 추천에서 고친 마감일
    return out


def todo_at(state: AppState, key: str) -> Todo | None:
    """"mid:index" → 그 액션 아이템 (마감일 수정 반영)."""
    mid, _, idx = key.rpartition(":")
    return dict(todo_items(state, mid)).get(int(idx)) if idx.isdigit() else None


def todo_owner_names(state: AppState, key: str, t: Todo) -> list[str]:
    """지금 담당자 이름들: 담당자 추천 · 지정에서 바꿨으면 그 사람들, 아니면 회의록의 담당자."""
    return [assignee_name(who) for who in todo_owner_ids(state, key, t)]


def todo_owner_ids(state: AppState, key: str, t: Todo) -> tuple[str, ...]:
    """지금 담당자 user_id 들: 담당자 추천 · 지정에서 바꿨으면 그 사람들, 아니면 회의록의 담당자."""
    return tuple(state.assigned[key]) if state.assigned.get(key) else t.owners


def mine_count(mid: str) -> int:
    return sum(1 for t in repo.meeting(mid).todos if me_id() in t.owners)


def my_todo_keys(state: AppState, mid: str) -> list[str]:
    """이 회의에서 나에게 맡겨진 할 일 (담당자 지정으로 바뀐 것까지 반영)."""
    keys = []
    for i, t in todo_items(state, mid):
        key = todo_key(mid, i)
        owner_is_me = me_id() in state.assigned[key] if state.assigned.get(key) else me_id() in t.owners
        if owner_is_me:
            keys.append(key)
    return keys


def my_todo_status(state: AppState, mid: str) -> str:
    """left(나에게 맡겨진 할 일이 있음) | none(없음)"""
    return "left" if my_todo_keys(state, mid) else "none"


def todo_key(mid: str, index: int) -> str:
    return f"{mid}:{index}"


def unassigned_count(state: AppState, mid: str) -> int:
    return sum(1 for i, t in todo_items(state, mid) if not t.owners and not state.assigned.get(todo_key(mid, i)))


def status_of(state: AppState, mid: str) -> str:
    """review | confirmed | sending | fail | done

    외부 도구(MCP) 전송에 실패하고 아직 해결되지 않은 회의는 「전송 실패」.
    """
    if mid in state.status_override:
        o = state.status_override[mid]
        if o == "fail" and repo.export_failures(mid) and not open_export_failure(state, mid):
            return "done"  # 관리자가 실패한 전송을 다시 보내 해결함 → 전달 완료
        return o
    if open_export_failure(state, mid):
        return "fail"
    return repo.meeting(mid).initial_status


def open_export_failure(state: AppState, mid: str):
    """아직 해결되지 않은 외부 도구 전송 실패 (없으면 None)."""
    return next((f for f in repo.export_failures(mid) if failure_open(state, f.id)), None)


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
    absent_shared: bool = False  # 미참석했지만 주관자가 공유해 줌


_STATUS_VIEW = {
    "review": ("검토 대기", "warn"),
    "confirmed": ("확정 · 전달 전", "ok"),
    "sending": ("전달 중", "host"),
    "fail": ("전송 실패", "fail"),
    "done": ("전달 완료", "hold"),
}


def _fail_sub(state: AppState, mid: str) -> str:
    f = open_export_failure(state, mid)
    if not f:
        return "일부 참석자 미전달"
    tool = repo.tool(f.tool).name
    return f"{tool} 연동 문제 · 관리자 문의 필요" if f.kind == "tech" else f"{tool} 전송 실패 · 다시 보내기"


def meeting_row(state: AppState, mid: str) -> MeetingRow:
    m = repo.meeting(mid)
    st = status_of(state, mid)
    is_host = m.host == me_id()
    month, day = m.date.split("/")
    dotted = m.date.replace("/", ".")
    sub_by_status = {
        "review": "확정 전 검토가 필요해요",
        "confirmed": f"{dotted} 확정 · 버전 1",
        "sending": "참석자에게 보내는 중",
        "fail": _fail_sub(state, mid),
        "done": f"{dotted} 전달",
    }
    button, kind = "결과 보기", "view"
    if manages(m):  # 재시도 · 검토 · 전달은 고칠 수 있는 사람만 (주관자 · 검토자)
        button, kind = {
            "review": ("검토하기", "review"),
            "confirmed": ("전달하기", "deliver"),
            "fail": ("외부 도구\n재전송" if open_export_failure(state, mid) else "결과 재전송", "retry"),
            "sending": ("진행 보기", "progress"),
        }.get(st, (button, kind))
    mine_keys = my_todo_keys(state, mid)
    mine_text = f"내 할 일 {len(mine_keys)}" if mine_keys else "내 할 일 없음"
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
        actions=len(todo_items(state, mid)),
        action_sub=mine_text + (f" · 담당 미정 {unassigned}" if (unassigned := unassigned_count(state, mid)) else ""),
        button=button,
        button_kind=kind,
        absent_shared=is_absent_shared(state, mid),
    )


# ---------------------------------------------------------------- 최근 회의록 (작성 화면)
def home_meeting_ids(state: AppState) -> list[str]:
    """나에게 보이는 회의 (주관 · 참석 · 미참석 공유받음, 최근 순)."""
    return by_date_desc(list(dict.fromkeys(visible_note_ids(state))))


# ---------------------------------------------------------------- 상단 경로
def breadcrumbs(state: AppState) -> list[str]:
    if state.not_found:
        return ["페이지를 찾을 수 없음"]
    s = state.screen
    types = repo.meeting_types()
    fs = folders(state)
    if s == "compose":
        return ["새 회의록"]
    if s == "search":
        return ["회의록", "검색 결과"]
    if s == "sent":
        return ["공유", "공유한 회의"]
    if s == "recv":
        return ["공유", "공유 받은 회의"]
    if s == "ask":
        return ["회의에 물어보기"]
    if s == "adm_cost":
        return ["관리자", "비용 관리"]
    if s == "adm_members":
        return ["관리자", "구성원 관리"]
    if s == "adm_mcp":
        return ["관리자", "MCP 관리"]
    if s == "archive":
        crumbs = ["회의록"]
        if state.archive_type:
            crumbs.append(types[state.archive_type].name)
        return crumbs
    if s == "folder":
        crumbs = ["회의록", "프로젝트"]
        if state.folder_id in fs:
            crumbs.append(fs[state.folder_id].name)
        return crumbs
    if s == "note":
        m = repo.meeting(state.meeting_id)
        if m.project_id in fs:
            return ["회의록", types[m.directory_id].name, fs[m.project_id].name, m.title]  # 회의 종류 / 프로젝트 (머리말 폴더 이름과 같은 순서)
        if is_reviewer(m):  # 프로젝트 멤버가 아닌 검토자
            return ["검토 요청 받은 회의", m.title]
        return ["공유 받은 회의", m.title]
    if s == "deliver":
        return ["회의록", repo.meeting(state.delivery.meeting_id).title, "결과 전달"]
    return ["새 회의록"]


def has_integration_warning(state: AppState) -> bool:
    return any(v == "expired" for v in repo.tool_statuses().values())


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
    return sum(1 for n in notifications(state) if n.unread and n.id not in state.read_notifications)



def notifications(state: AppState) -> list:
    """이 계정의 알림 (관리자 · 사용자)."""
    return repo.notifications("admin" if state.is_admin else "user")


def is_unread(state: AppState, nid: str) -> bool:
    n = next(x for x in notifications(state) if x.id == nid)
    return n.unread and nid not in state.read_notifications


def show_bell_dot(state: AppState) -> bool:
    return any(is_unread(state, n.id) and n.id not in state.bell_seen for n in notifications(state))


# ---------------------------------------------------------------- 관리자
ADMIN_SCREENS = ("adm_cost", "adm_members", "adm_mcp")
ADMIN_SELF = "admin"  # 관리자 계정 자신 (구성원 목록 맨 위, 권한·상태 못 바꿈)


@dataclass(frozen=True)
class AdminMember:
    id: str
    name: str
    email: str
    team: str
    job: str
    role: str  # admin | member (웹 권한)
    status: str  # active | inactive
    last: str  # 최근 접속
    is_self: bool = False
    position: str = ""  # senior | mid | junior
    skills: tuple[str, ...] = ()  # 「스킬 이름 Lv.n」


def admin_profile(state: AppState) -> dict:
    """로그인한 관리 계정 (사이드바 카드 · 구성원 목록 맨 위)."""
    return {"name": state.user_name, "email": state.user_email, "team": "관리 계정"}


def admin_members(state: AppState) -> list[AdminMember]:
    """구성원 관리 목록: 관리자 계정 → 조직 구성원 (Neo4j, 팀 순)."""
    me = admin_profile(state)
    rows = [AdminMember(ADMIN_SELF, me["name"], me["email"], me["team"], "시스템 관리", "admin", "active", "지금 접속 중", True)]
    people = sorted(repo.people_rows(), key=lambda r: r["team"] or "")
    for r in people:
        rows.append(AdminMember(r["user_id"], r["name"], r["email"] or "-", r["team"] or "-", r["job"] or "-",
                                r["permission"], "active" if r["is_active"] is not False else "inactive",
                                "-",  # shortcut: 접속 기록이 없어 "-", 로그인 작업 때 채움
                                position=r["position"] or "",
                                skills=tuple(f"{k['name']} Lv.{k['level']}" for k in r["skills"])))
    return rows


def admin_member(state: AppState, pid: str) -> AdminMember:
    return next(m for m in admin_members(state) if m.id == pid)


def _org_walk(nodes: list[dict], path: tuple[str, ...] = ()):
    """조직도를 돌며 (이름, 위 단계들, 자식 있는지) 를 차례로."""
    for n in nodes:
        yield n["name"], path, bool(n.get("children"))
        yield from _org_walk(n.get("children", []), path + (n["name"],))


def admin_teams() -> list[str]:
    """고를 수 있는 팀: 조직도 순서 → 조직도에 없는 팀."""
    in_tree = [name for name, _, has_kids in _org_walk(repo.org_tree()) if not has_kids]
    extra = sorted(({t.name for t in repo.teams().values()} | {p.team for p in repo.people().values()}) - set(in_tree))
    return in_tree + extra


def team_ancestors(team: str) -> tuple[str, ...]:
    """팀의 위 단계 (본부, 부)."""
    return next((path for name, path, _ in _org_walk(repo.org_tree()) if name == team), ())


def team_path(team: str) -> str:
    """「제품본부 › 프로덕트부」 (조직도에 없으면 빈 값)."""
    return " › ".join(team_ancestors(team))


def failure_open(state: AppState, fid: str) -> bool:
    """아직 다시 보내지 않은 전송 실패인지."""
    return fid not in state.export_resent and not repo.is_resolved(fid)  # 내가 다시 보냄 · 관리자가 해결함
