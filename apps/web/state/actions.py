# """상태 변경 함수 모음.

# 버튼의 on_click 콜백으로 바로 넘길 수 있도록 모두 `state` 를 직접 받지 않고 get_state() 로 꺼내 씁니다.
# 화면 코드는 상태를 직접 고치지 말고 여기 있는 함수만 호출하세요.
# """

# from __future__ import annotations

# import time

# import streamlit as st
# from config import DEMO_EMAIL, DEMO_PASSWORD
# from data import repository as repo
# from state import selectors as sel
# from state import router
# from state.store import Delivery, NewFolder, SentShare, get_state, reset_state

# # 화면을 옮길 때 항상 초기화하는 값
# _SCREEN_RESET = dict(editing=False, highlight=[], highlight_key="", show_brief=False, raw_query="", copied="")


# def _set(**kwargs) -> None:
#     s = get_state()
#     for k, v in kwargs.items():
#         setattr(s, k, v)


# def update(**kwargs) -> None:
#     """단순 값 변경용 콜백. 예: on_click=update, kwargs={"home_role": "host"}"""
#     _set(**kwargs)


# def toggle(attr: str) -> None:
#     s = get_state()
#     setattr(s, attr, not getattr(s, attr))


# # ---------------------------------------------------------------- 인증
# def login(email: str, password: str) -> bool:
#     ok = email.strip() == DEMO_EMAIL and password == DEMO_PASSWORD
#     _set(logged_in=ok, login_error=not ok, screen="home")
#     return ok


# def demo_login() -> None:
#     _set(logged_in=True, login_error=False, screen="home")


# def logout() -> None:
#     reset_state(logged_out=True)
#     router.request("home")


# # ---------------------------------------------------------------- 이동
# def go(screen: str, **extra) -> None:
#     """화면 이동: 상태를 정리하고 URL 이동을 예약합니다 (실제 이동은 app.py 의 router.apply_pending)."""
#     _set(screen=screen, **_SCREEN_RESET, **extra)
#     router.request(screen)


# def open_folder_type(mtype: str) -> None:
#     go("folder", folder_type=mtype, folder_id="all", folder_owner_filter="all")


# def open_folder(fid: str) -> None:
#     f = sel.folders(get_state())[fid]
#     go("folder", folder_type=f.type, folder_id=fid, folder_tab="all", folder_role="all")


# def open_meeting(mid: str, *, editing: bool = False) -> None:
#     go("note", meeting_id=mid, insight_tab="sum", left_tab="raw", just_shared=False, todo_scope="mine")
#     _set(editing=editing)


# def open_source(mid: str, src: tuple[str, ...]) -> None:
#     """위키 타임라인의 출처 → 해당 회의의 원문 발언 강조."""
#     go("note", meeting_id=mid, insight_tab="sum", left_tab="raw", just_shared=False)
#     _set(highlight=list(src), highlight_key="wiki")


# def close_toast() -> None:
#     _set(toast="")


# # ---------------------------------------------------------------- 모달
# def open_modal(name: str, **extra) -> None:
#     _set(modal=name, **extra)


# def close_modal() -> None:
#     _set(modal=None, nn_error=False, assign_pending="")


# def open_new_note() -> None:
#     s = get_state()
#     mtype = s.folder_type if s.screen == "folder" else "weekly"
#     if s.screen == "folder" and s.folder_id != "all":
#         topic = s.folder_id
#     else:
#         topic = (sel.folder_ids_of_type(s, mtype) or ["new"])[0]
#     open_modal("new_note", nn_type=mtype, nn_topic=topic, nn_name="", nn_file="", nn_error=False)


# def set_new_note_type(mtype: str) -> None:
#     s = get_state()
#     _set(nn_type=mtype, nn_topic=(sel.folder_ids_of_type(s, mtype) or ["new"])[0])


# def save_new_note() -> bool:
#     """성공하면 True (모달 닫기)."""
#     s = get_state()
#     if not s.nn_file:
#         s.nn_error = True
#         return False
#     topics = sel.folder_ids_of_type(s, s.nn_type)
#     topic = s.nn_topic if s.nn_topic in topics else "new"
#     file_name = s.nn_file
#     if topic == "new":
#         fid = f"n{len(s.new_folders) + 1}"
#         name = s.nn_name.strip() or "새 주제"
#         s.new_folders.append(NewFolder(fid, name, s.nn_type))
#         toast = f"「{name}」 폴더를 만들고 {file_name}을(를) 올렸어요. 정리가 끝나면 알려드릴게요."
#     else:
#         fid = topic
#         toast = f"「{sel.folders(s)[fid].name}」에 {file_name}을(를) 올렸어요. 정리가 끝나면 알려드릴게요."
#     go("folder", folder_type=s.nn_type, folder_id=fid, folder_tab="all", folder_role="all")
#     _set(modal=None, nn_name="", nn_file="", nn_error=False, toast=toast)
#     return True


# # ---------------------------------------------------------------- 폴더
# def add_member(fid: str, pid: str = "dw") -> None:
#     s = get_state()
#     cur = s.members_added.setdefault(fid, [])
#     if pid not in cur and pid not in sel.members_of(s, fid):
#         cur.append(pid)
#     s.members_removed.discard(f"{fid}:{pid}")

# def add_members(fid: str, pids: list[str]) -> None:
#     """멤버 관리 모달: 팀에서 고른 사람들을 한 번에 추가."""
#     for pid in pids:
#         add_member(fid, pid)
#     get_state().member_nonce += 1


# def add_picked_members(fid: str, widget_key: str) -> None:
#     """팀원 칩을 누르는 즉시(on_change) 멤버로 추가 → 목록에 바로 반영."""
#     add_members(fid, list(st.session_state.get(widget_key) or []))


# def remove_member(fid: str, pid: str) -> None:
#     get_state().members_removed.add(f"{fid}:{pid}")


# def delete_folder(fid: str) -> None:
#     s = get_state()
#     name = sel.folders(s)[fid].name
#     s.deleted_folders.add(fid)
#     go("folder", folder_id="all")  # URL 도 회의 종류 화면으로
#     _set(modal=None, toast=f"「{name}」 폴더를 삭제했어요.")


# # ---------------------------------------------------------------- 회의 상세
# def pick_evidence(key: str, src: tuple[str, ...]) -> None:
#     """요약·결정 문장 클릭 → 원문 근거 강조 (같은 걸 다시 누르면 해제)."""
#     s = get_state()
#     m = repo.meeting(s.meeting_id)
#     if (m.shared and m.shared.lock_raw) or not src:
#         return
#     if s.highlight_key == key:
#         _set(highlight=[], highlight_key="")
#     else:
#         _set(highlight=list(src), highlight_key=key, left_tab="raw", raw_query="")


# def clear_highlight() -> None:
#     _set(highlight=[], highlight_key="")


# def set_insight_tab(tab: str) -> None:
#     _set(insight_tab=tab, highlight=[], highlight_key="")


# def search_keyword(keyword: str) -> None:
#     s = get_state()
#     if s.raw_query == keyword:
#         _set(raw_query="")
#     else:
#         _set(raw_query=keyword, left_tab="raw", highlight=[], highlight_key="")


# def toggle_todo(key: str) -> None:
#     s = get_state()
#     s.done[key] = not s.done.get(key, False)


# def mark_copied(key: str) -> None:
#     _set(copied=key)


# def start_edit() -> None:
#     _set(editing=True, just_shared=False)


# def stop_edit() -> None:
#     _set(editing=False)


# def branch_agenda() -> None:
#     _set(branched=True)

# def open_share() -> None:
#     s = get_state()
#     open_modal("share", share_nonce=s.share_nonce + 1, share_scope="summary", share_team="", share_recipients=[])


# def set_share_team_selection(team_id: str, widget_key: str) -> None:
#     """공유 모달: 지금 보고 있는 팀에서 선택된 사람으로 받는 사람을 갱신 (다른 팀 선택은 그대로 유지)."""
#     s = get_state()
#     team_ids = {mb.id for mb in repo.teams()[team_id].members}
#     picked = list(st.session_state.get(widget_key) or [])
#     s.share_recipients = [pid for pid in s.share_recipients if pid not in team_ids] + picked


# def share_summary(recipients: dict[str, list[str]]) -> str:
#     """{'재무팀': ['강수현', '오지훈']} → '재무팀(강수현 외 1명)'"""
#     parts = []
#     for team, names in recipients.items():
#         parts.append(f"{team}({names[0]}" + (f" 외 {len(names) - 1}명" if len(names) > 1 else "") + ")")
#     return ", ".join(parts)


# def confirm_share(recipients: dict[str, list[str]]) -> None:
#     """recipients: 팀 이름 → 고른 구성원 이름 목록"""
#     s = get_state()
#     to = share_summary(recipients)
#     s.sent_shares.insert(0, SentShare(s.meeting_id, to, s.share_scope, "11/1"))
#     _set(modal=None, just_shared=True, last_share_to=to)


# def revoke_share(index: int) -> None:
#     s = get_state()
#     item = s.sent_shares.pop(index)
#     s.toast = f"「{repo.meeting(item.meeting_id).title}」 공유를 취소했어요."


# # ---------------------------------------------------------------- 담당자 지정
# def open_assign(key: str, text: str) -> None:
#     open_modal(
#         "assign",
#         assign_key=key,
#         assign_text=text,
#         assign_searching=False,
#         assign_pending="",
#         assign_query="",
#         assign_reason="",
#         assign_team="",
#     )


# def assign_to(name: str) -> None:
#     s = get_state()
#     s.assigned[s.assign_key] = name
#     _set(
#         modal=None,
#         assign_pending="",
#         last_assign=f"{s.assign_text} → {name} 님에게 맡겼어요. 확정하기 전까지 바꿀 수 있고, 확정하면 알림이 가요.",
#     )


# # ---------------------------------------------------------------- 결과 전달
# def start_delivery(mid: str, attempt: int = 1) -> None:
#     s = get_state()
#     s.delivery = Delivery(meeting_id=mid, attempt=attempt, started_at=time.time(), finished=False)
#     s.status_override[mid] = "sending"
#     go("deliver")


# def run_row_action(mid: str, kind: str) -> None:
#     """표의 '관리' 버튼."""
#     if kind == "review":
#         open_meeting(mid, editing=True)
#     elif kind == "deliver":
#         start_delivery(mid, 1)
#     elif kind == "retry":
#         start_delivery(mid, 2)
#     elif kind == "progress":
#         get_state().delivery.meeting_id = mid
#         go("deliver")
#     else:
#         open_meeting(mid)


# # ---------------------------------------------------------------- 외부 연동
# def set_integration(tool_id: str) -> None:
#     s = get_state()
#     tool = next(t for t in repo.integrations()["tools"] if t.id == tool_id)
#     cur = s.integrations.get(tool_id, "off")
#     if cur == "on":
#         s.integrations[tool_id] = "off"
#         s.toast = f"{tool.name} 연결을 해제했어요."
#     elif cur == "expired":
#         s.integrations[tool_id] = "on"
#         s.toast = f"{tool.name} 인증을 갱신했어요. 다시 전송할 수 있어요."
#     elif tool.phase2:
#         s.toast = f"{tool.name} 연동은 2단계(Phase 2)에서 제공될 예정이에요."
#     else:
#         s.integrations[tool_id] = "on"
#         s.toast = f"{tool.name}을(를) 연결했어요."


# def toggle_ai_folder(fid: str) -> None:
#     s = get_state()
#     s.ai_folders[fid] = not s.ai_folders.get(fid, False)


# def toggle_ai_option(key: str) -> None:
#     s = get_state()
#     s.ai_toggles[key] = not s.ai_toggles.get(key, False)



# # ---------------------------------------------------------------- 알림
# def open_bell() -> None:
#     s = get_state()
#     s.bell_seen |= {n.id for n in repo.notifications()}


# def open_notification(nid: str) -> None:
#     s = get_state()
#     s.read_notifications.add(nid)
#     n = next(x for x in repo.notifications() if x.id == nid)
#     if n.meeting:
#         open_meeting(n.meeting)
#     elif n.kind == "integration":
#         go("mcp")

# def add_picked_members(fid: str, widget_key: str) -> None:
#     """팀원 칩을 누르는 즉시(on_change) 멤버로 추가 → 목록에 바로 반영."""
#     add_members(fid, list(st.session_state.get(widget_key) or []))
#     get_state().member_just_added = True  # 모달이 다시 그려질 때 목록 맨 아래로 이동


"""상태 변경 함수 모음.

버튼의 on_click 콜백으로 바로 넘길 수 있도록 모두 `state` 를 직접 받지 않고 get_state() 로 꺼내 씁니다.
화면 코드는 상태를 직접 고치지 말고 여기 있는 함수만 호출하세요.
"""

from __future__ import annotations

import time

import streamlit as st

from config import CURRENT_USER_ID as ME
from config import DEMO_EMAIL, DEMO_PASSWORD
from data import repository as repo
from state import router
from state import selectors as sel
from state.store import Delivery, NewFolder, SentShare, get_state, reset_state

# 화면을 옮길 때 항상 초기화하는 값
_SCREEN_RESET = dict(editing=False, highlight=[], highlight_key="", show_brief=False, raw_query="", copied="", nn_error="")


def _set(**kwargs) -> None:
    s = get_state()
    for k, v in kwargs.items():
        setattr(s, k, v)


def update(**kwargs) -> None:
    """단순 값 변경용 콜백. 예: on_click=update, kwargs={"home_role": "host"}"""
    _set(**kwargs)


def toggle(attr: str) -> None:
    s = get_state()
    setattr(s, attr, not getattr(s, attr))


# ---------------------------------------------------------------- 인증
def login(email: str, password: str) -> bool:
    ok = email.strip() == DEMO_EMAIL and password == DEMO_PASSWORD
    _set(logged_in=ok, login_error=not ok, screen="compose")
    return ok


def demo_login() -> None:
    _set(logged_in=True, login_error=False, screen="compose")


def logout() -> None:
    reset_state(logged_out=True)
    router.request("compose")


# ---------------------------------------------------------------- 이동
def go(screen: str, **extra) -> None:
    """화면 이동: 상태를 정리하고 URL 이동을 예약합니다 (실제 이동은 app.py 의 router.apply_pending)."""
    _set(screen=screen, **{**_SCREEN_RESET, **extra})
    router.request(screen)


def open_archive(mtype: str = "", group: str = "", period: str = "") -> None:
    """회의록 화면: 회의록 → 회의 종류 → 주차별(월별·일별)/프로젝트별 → 기간."""
    s = get_state()
    extra = {}
    if group == "project":
        extra["folder_owner_filter"] = "all"
    if group == "period" and not period and s.archive_group != "period":
        extra["archive_owner"] = "all"  # 기간 목록에 새로 들어올 때만 필터를 처음으로
    if period:
        extra.update(folder_role="all", folder_tab="all", period_wiki_folder="", todo_group="")
    go("archive", archive_type=mtype, archive_group=group, archive_period=period, **extra)


def open_folder_type(mtype: str) -> None:
    open_archive(mtype)


def tree_toggle(node: str) -> None:
    """사이드바 트리 화살표: 화면 이동 없이 바로 펼치기/접기."""
    s = get_state()
    if node in s.tree_open:
        s.tree_open.discard(node)
    else:
        s.tree_open.add(node)


def tree_click(node: str) -> None:
    """사이드바 트리 폴더 이름: 그 화면으로 이동 + 펼치기 (접기는 화살표로)."""
    s = get_state()
    s.tree_open.add(node)
    kind, *rest = node.split(":")
    if kind == "root":
        open_archive()
    elif kind == "t":
        open_archive(rest[0])
    elif kind == "g":
        open_archive(rest[0], rest[1])
    elif kind == "p":
        open_archive(rest[0], "period", rest[1])
    elif kind == "f":
        open_folder(rest[1])


def open_folder(fid: str) -> None:
    f = sel.folders(get_state())[fid]
    go("folder", folder_type=f.type, folder_id=fid, folder_tab="all", folder_role="all", todo_group="")


def open_meeting(mid: str, *, editing: bool = False) -> None:
    go("note", meeting_id=mid, insight_tab="sum", left_tab="raw", just_shared=False, todo_scope="mine")
    _set(editing=editing)


def open_source(mid: str, src: tuple[str, ...]) -> None:
    """위키 타임라인의 출처 → 해당 회의의 원문 발언 강조."""
    go("note", meeting_id=mid, insight_tab="sum", left_tab="raw", just_shared=False)
    _set(highlight=list(src), highlight_key="wiki")


def close_toast() -> None:
    _set(toast="")


# ---------------------------------------------------------------- 모달
def open_modal(name: str, **extra) -> None:
    _set(modal=name, **extra)


def close_modal() -> None:
    _set(modal=None, assign_pending="", members_folder="", members_scope=[], delete_target=())


def open_new_note() -> None:
    """'새 회의록' → 작성 화면. 보고 있던 폴더·회의 종류를 미리 골라 둡니다."""
    s = get_state()
    if s.screen == "folder" and s.folder_id in sel.folders(s):
        mtype, topic = s.folder_type, s.folder_id
    else:
        mtype = s.archive_type if s.screen == "archive" and s.archive_type else s.nn_type
        topic = (sel.folder_ids_of_type(s, mtype) or ["new"])[0]
        if s.screen == "archive" and s.archive_period:
            from state import archive  # archive 가 selectors 를 써서 함수 안에서 불러옴

            p = archive.period(s, mtype, s.archive_period)
            if p:
                topic = archive.period_folder_ids(s, p)[0]
    go("compose", nn_type=mtype, nn_topic=topic, nn_name="", nn_host=ME, nn_people=[],
       compose_nonce=s.compose_nonce + 1)


def set_new_note_type(mtype: str) -> None:
    s = get_state()
    _set(nn_type=mtype, nn_topic=(sel.folder_ids_of_type(s, mtype) or ["new"])[0])


def submit_compose(nonce: int) -> None:
    """작성 화면 「회의록 생성하기」: 내용(글 또는 파일) + 제목·날짜·주관자를 확인하고 올립니다."""
    s = get_state()
    ss = st.session_state
    text = (ss.get(f"cp_text_{nonce}") or "").strip()
    files = [f.name for f in (ss.get(f"cp_files_{nonce}") or [])]
    title = (ss.get(f"cp_title_{nonce}") or "").strip()
    day = ss.get(f"cp_date_{nonce}")
    host = s.nn_host
    missing = [name for name, ok in (("회의 내용(글 또는 파일)", text or files), ("회의 제목", title),
                                     ("회의 날짜", day), ("주관자", host)) if not ok]
    # 고른 값은 위젯에서 바로 읽음 (on_change 보다 버튼이 먼저 눌려도 맞도록)
    s.nn_type = ss.get(f"cp_type_{nonce}") or s.nn_type
    topics = sel.folder_ids_of_type(s, s.nn_type)
    topic = s.nn_topic if s.nn_topic in topics else "new"
    if topic == "new" and not s.nn_name.strip():
        missing.append("새 폴더 이름")
    if missing:
        s.nn_error = " · ".join(missing) + "을(를) 입력해 주세요."
        return
    what = f"「{title}」"
    if topic == "new":
        fid = f"n{len(s.new_folders) + 1}"
        name = s.nn_name.strip()
        s.new_folders.append(NewFolder(fid, name, s.nn_type))
        toast = f"「{name}」 폴더를 만들고 {what} 회의록을 올렸어요. 정리가 끝나면 알려드릴게요."
    else:
        fid = topic
        toast = f"「{sel.folders(s)[fid].name}」에 {what} 회의록을 올렸어요. 정리가 끝나면 알려드릴게요."
    go("folder", folder_type=s.nn_type, folder_id=fid, folder_tab="all", folder_role="all", todo_group="")
    _set(nn_name="", nn_error="", nn_host=ME, nn_people=[], toast=toast, compose_nonce=nonce + 1)  # 입력칸 비우기
    s.tree_open |= {"root", f"t:{s.nn_type}", f"g:{s.nn_type}:project"}


# ---------------------------------------------------------------- 작성 화면 · 선택 모달
def open_picker(kind: str) -> None:
    """kind: cp_folder(폴더) | cp_host(주관자) | cp_people(참석자) — 지금 값을 임시 선택으로 복사해 모달 열기."""
    s = get_state()
    draft = [s.nn_host] if kind == "cp_host" else list(s.nn_people)
    first = draft[0] if draft else ME
    open_modal(kind, pick_draft=draft, pick_team=sel.person(first).team, pick_folder=s.nn_topic,
               pick_name=s.nn_name, pick_nonce=s.pick_nonce + 1)


def pick_people(team_ids: list[str], widget_key: str, single: bool) -> None:
    """팀원 칩을 누를 때: 이 팀에서 고른 사람으로 임시 선택을 갱신 (다른 팀에서 고른 사람은 그대로)."""
    s = get_state()
    value = st.session_state.get(widget_key)
    if single:
        s.pick_draft = [value] if value else []
    else:
        s.pick_draft = [p for p in s.pick_draft if p not in team_ids] + list(value or [])


def confirm_pick(kind: str) -> None:
    s = get_state()
    if kind == "cp_host":
        s.nn_host = s.pick_draft[0] if s.pick_draft else s.nn_host
    elif kind == "cp_people":
        s.nn_people = list(s.pick_draft)
    elif kind == "cp_folder":
        s.nn_topic, s.nn_name = s.pick_folder, s.pick_name.strip()
    s.nn_error = ""
    close_modal()


# ---------------------------------------------------------------- 폴더
def add_member(fid: str, pid: str = "dw") -> None:
    s = get_state()
    cur = s.members_added.setdefault(fid, [])
    if pid not in cur and pid not in sel.members_of(s, fid):
        cur.append(pid)
    s.members_removed.discard(f"{fid}:{pid}")

def add_members(fid: str, pids: list[str]) -> None:
    """멤버 관리 모달: 팀에서 고른 사람들을 한 번에 추가."""
    for pid in pids:
        add_member(fid, pid)
    get_state().member_nonce += 1


def remove_member(fid: str, pid: str) -> None:
    get_state().members_removed.add(f"{fid}:{pid}")


def open_members(fid: str, scope: list[str] | None = None) -> None:
    """멤버 모달. scope 를 주면(기간 폴더) 그 안의 프로젝트 폴더를 골라 가며 관리."""
    open_modal("members", members_folder=fid, members_scope=list(scope or []))


def open_delete(*target: str) -> None:
    """삭제 확인 모달. target: ("folder", fid) | ("period", 회의 종류, 기간 키) | ("meeting", mid)"""
    open_modal("delete_meeting" if target[0] == "meeting" else "delete_folder", delete_target=tuple(target))


def delete_period(mtype: str, key: str, ids: list[str]) -> None:
    """기간 폴더 삭제: 그 기간의 (내가 주관한 폴더의) 회의록을 모두 지움."""
    from state import archive

    s = get_state()
    s.deleted_meetings |= set(ids)
    open_archive(mtype, "period")
    _set(modal=None, delete_target=(), toast=f"「{archive.period_label(key)}」 폴더의 회의록 {len(ids)}개를 삭제했어요.")


def delete_meeting(mid: str) -> None:
    """주관자: 회의 삭제 → 그 회의가 있던 폴더로."""
    s = get_state()
    m = repo.meeting(mid)
    s.deleted_meetings.add(mid)
    if m.topic in sel.folders(s):
        open_folder(m.topic)
    else:
        go("compose")
    _set(modal=None, delete_target=(), toast=f"「{m.title}」 회의를 삭제했어요.")


def delete_folder(fid: str) -> None:
    s = get_state()
    name = sel.folders(s)[fid].name
    mtype = sel.folders(s)[fid].type
    s.deleted_folders.add(fid)
    open_archive(mtype, "project")  # URL 도 프로젝트별 폴더 목록으로
    _set(modal=None, delete_target=(), toast=f"「{name}」 폴더를 삭제했어요.")


# ---------------------------------------------------------------- 회의 상세
def pick_evidence(key: str, src: tuple[str, ...]) -> None:
    """요약·결정 문장 클릭 → 원문 근거 강조 (같은 걸 다시 누르면 해제)."""
    s = get_state()
    m = repo.meeting(s.meeting_id)
    if (m.shared and m.shared.lock_raw) or not src:
        return
    if s.highlight_key == key:
        _set(highlight=[], highlight_key="")
    else:
        _set(highlight=list(src), highlight_key=key, left_tab="raw", raw_query="")


def clear_highlight() -> None:
    _set(highlight=[], highlight_key="")


def set_insight_tab(tab: str) -> None:
    _set(insight_tab=tab, highlight=[], highlight_key="")


def search_keyword(keyword: str) -> None:
    s = get_state()
    if s.raw_query == keyword:
        _set(raw_query="")
    else:
        _set(raw_query=keyword, left_tab="raw", highlight=[], highlight_key="")


def toggle_todo(key: str) -> None:
    s = get_state()
    s.done[key] = not s.done.get(key, False)


def mark_copied(key: str) -> None:
    _set(copied=key)


def start_edit() -> None:
    _set(editing=True, just_shared=False)


def stop_edit() -> None:
    _set(editing=False)


def branch_agenda() -> None:
    _set(branched=True)

def open_share() -> None:
    s = get_state()
    open_modal("share", share_nonce=s.share_nonce + 1, share_scope="summary", share_team="", share_recipients=[])


def set_share_team_selection(team_id: str, widget_key: str) -> None:
    """공유 모달: 지금 보고 있는 팀에서 선택된 사람으로 받는 사람을 갱신 (다른 팀 선택은 그대로 유지)."""
    s = get_state()
    team_ids = {mb.id for mb in repo.teams()[team_id].members}
    picked = list(st.session_state.get(widget_key) or [])
    s.share_recipients = [pid for pid in s.share_recipients if pid not in team_ids] + picked


def set_share_absent_selection(absent: list[str], widget_key: str) -> None:
    """공유 모달: 빠진 멤버 선택 → 받는 사람 갱신 (팀에서 고른 사람은 그대로)."""
    s = get_state()
    picked = list(st.session_state.get(widget_key) or [])
    s.share_recipients = [pid for pid in s.share_recipients if pid not in absent] + picked


def share_summary(recipients: dict[str, list[str]]) -> str:
    """{'재무팀': ['강수현', '오지훈']} → '재무팀(강수현 외 1명)'"""
    parts = []
    for team, names in recipients.items():
        parts.append(f"{team}({names[0]}" + (f" 외 {len(names) - 1}명" if len(names) > 1 else "") + ")")
    return ", ".join(parts)


def confirm_share(recipients: dict[str, list[str]]) -> None:
    """recipients: 팀 이름 → 고른 구성원 이름 목록"""
    s = get_state()
    to = share_summary(recipients)
    # 이 회의에 빠진 사람에게 보냈다면 → 그 사람에게 이 회의가 「미참석 · 공유받음」으로 보이게 기록
    m = repo.meeting(s.meeting_id)
    absent = [pid for pid in s.share_recipients if pid not in m.people and pid != m.host]
    if absent:
        cur = s.absent_shares.setdefault(s.meeting_id, [])
        cur.extend(pid for pid in absent if pid not in cur)
    s.sent_shares.insert(0, SentShare(s.meeting_id, to, s.share_scope, "11/1"))
    _set(modal=None, just_shared=True, last_share_to=to)


def revoke_share(index: int) -> None:
    s = get_state()
    item = s.sent_shares.pop(index)
    s.toast = f"「{repo.meeting(item.meeting_id).title}」 공유를 취소했어요."


# ---------------------------------------------------------------- 담당자 지정
def open_assign(key: str, text: str) -> None:
    open_modal(
        "assign",
        assign_key=key,
        assign_text=text,
        assign_searching=False,
        assign_pending="",
        assign_query="",
        assign_reason="",
        assign_team="",
    )


def assign_to(name: str) -> None:
    s = get_state()
    s.assigned[s.assign_key] = name
    _set(
        modal=None,
        assign_pending="",
        last_assign=f"{s.assign_text} → {name} 님에게 맡겼어요. 확정하기 전까지 바꿀 수 있고, 확정하면 알림이 가요.",
    )


# ---------------------------------------------------------------- 결과 전달
def start_delivery(mid: str, attempt: int = 1) -> None:
    s = get_state()
    s.delivery = Delivery(meeting_id=mid, attempt=attempt, started_at=time.time(), finished=False)
    s.status_override[mid] = "sending"
    go("deliver")


def run_row_action(mid: str, kind: str) -> None:
    """표의 '관리' 버튼."""
    if kind == "review":
        open_meeting(mid, editing=True)
    elif kind == "deliver":
        start_delivery(mid, 1)
    elif kind == "retry":
        start_delivery(mid, 2)
    elif kind == "progress":
        get_state().delivery.meeting_id = mid
        go("deliver")
    else:
        open_meeting(mid)


# ---------------------------------------------------------------- 외부 연동
def set_integration(tool_id: str) -> None:
    s = get_state()
    tool = next(t for t in repo.integrations()["tools"] if t.id == tool_id)
    cur = s.integrations.get(tool_id, "off")
    if cur == "on":
        s.integrations[tool_id] = "off"
        s.toast = f"{tool.name} 연결을 해제했어요."
    elif cur == "expired":
        s.integrations[tool_id] = "on"
        s.toast = f"{tool.name} 인증을 갱신했어요. 다시 전송할 수 있어요."
    elif tool.phase2:
        s.toast = f"{tool.name} 연동은 2단계(Phase 2)에서 제공될 예정이에요."
    else:
        s.integrations[tool_id] = "on"
        s.toast = f"{tool.name}을(를) 연결했어요."


def toggle_ai_folder(fid: str) -> None:
    s = get_state()
    s.ai_folders[fid] = not s.ai_folders.get(fid, False)


def toggle_ai_option(key: str) -> None:
    s = get_state()
    s.ai_toggles[key] = not s.ai_toggles.get(key, False)



# ---------------------------------------------------------------- 알림
def open_bell() -> None:
    s = get_state()
    s.bell_seen |= {n.id for n in repo.notifications()}


def open_notification(nid: str) -> None:
    s = get_state()
    s.read_notifications.add(nid)
    n = next(x for x in repo.notifications() if x.id == nid)
    if n.meeting:
        open_meeting(n.meeting)
    elif n.kind == "integration":
        go("mcp")

def add_picked_members(fid: str, widget_key: str) -> None:
    """팀원 칩을 누르는 즉시(on_change) 멤버로 추가 → 목록에 바로 반영."""
    add_members(fid, list(st.session_state.get(widget_key) or []))
    get_state().member_just_added = True  # 모달이 다시 그려질 때 목록 맨 아래로 이동