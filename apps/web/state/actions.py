"""상태 변경 함수 모음.

버튼의 on_click 콜백으로 바로 넘길 수 있도록 모두 `state` 를 직접 받지 않고 get_state() 로 꺼내 씁니다.
화면 코드는 상태를 직접 고치지 말고 여기 있는 함수만 호출하세요.
"""

from __future__ import annotations

import time

import streamlit as st
from config import DEMO_EMAIL, DEMO_PASSWORD
from data import repository as repo
from state import selectors as sel
from state import router
from state.store import Delivery, NewFolder, SentShare, get_state, reset_state

# 화면을 옮길 때 항상 초기화하는 값
_SCREEN_RESET = dict(editing=False, highlight=[], highlight_key="", show_brief=False, raw_query="", copied="")


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
    _set(logged_in=ok, login_error=not ok, screen="home")
    return ok


def demo_login() -> None:
    _set(logged_in=True, login_error=False, screen="home")


def logout() -> None:
    reset_state(logged_out=True)
    router.request("home")


# ---------------------------------------------------------------- 이동
def go(screen: str, **extra) -> None:
    """화면 이동: 상태를 정리하고 URL 이동을 예약합니다 (실제 이동은 app.py 의 router.apply_pending)."""
    _set(screen=screen, **_SCREEN_RESET, **extra)
    router.request(screen)


def open_folder_type(mtype: str) -> None:
    go("folder", folder_type=mtype, folder_id="all", folder_owner_filter="all")


def open_folder(fid: str) -> None:
    f = sel.folders(get_state())[fid]
    go("folder", folder_type=f.type, folder_id=fid, folder_tab="all", folder_role="all")


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
    _set(modal=None, nn_error=False, assign_pending="")


def open_new_note() -> None:
    s = get_state()
    mtype = s.folder_type if s.screen == "folder" else "weekly"
    if s.screen == "folder" and s.folder_id != "all":
        topic = s.folder_id
    else:
        topic = (sel.folder_ids_of_type(s, mtype) or ["new"])[0]
    open_modal("new_note", nn_type=mtype, nn_topic=topic, nn_name="", nn_file="", nn_error=False)


def set_new_note_type(mtype: str) -> None:
    s = get_state()
    _set(nn_type=mtype, nn_topic=(sel.folder_ids_of_type(s, mtype) or ["new"])[0])


def save_new_note() -> bool:
    """성공하면 True (모달 닫기)."""
    s = get_state()
    if not s.nn_file:
        s.nn_error = True
        return False
    topics = sel.folder_ids_of_type(s, s.nn_type)
    topic = s.nn_topic if s.nn_topic in topics else "new"
    file_name = s.nn_file
    if topic == "new":
        fid = f"n{len(s.new_folders) + 1}"
        name = s.nn_name.strip() or "새 주제"
        s.new_folders.append(NewFolder(fid, name, s.nn_type))
        toast = f"「{name}」 폴더를 만들고 {file_name}을(를) 올렸어요. 정리가 끝나면 알려드릴게요."
    else:
        fid = topic
        toast = f"「{sel.folders(s)[fid].name}」에 {file_name}을(를) 올렸어요. 정리가 끝나면 알려드릴게요."
    go("folder", folder_type=s.nn_type, folder_id=fid, folder_tab="all", folder_role="all")
    _set(modal=None, nn_name="", nn_file="", nn_error=False, toast=toast)
    return True


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


def add_picked_members(fid: str, widget_key: str) -> None:
    """팀원 칩을 누르는 즉시(on_change) 멤버로 추가 → 목록에 바로 반영."""
    add_members(fid, list(st.session_state.get(widget_key) or []))


def remove_member(fid: str, pid: str) -> None:
    get_state().members_removed.add(f"{fid}:{pid}")


def delete_folder(fid: str) -> None:
    s = get_state()
    name = sel.folders(s)[fid].name
    s.deleted_folders.add(fid)
    go("folder", folder_id="all")  # URL 도 회의 종류 화면으로
    _set(modal=None, toast=f"「{name}」 폴더를 삭제했어요.")


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