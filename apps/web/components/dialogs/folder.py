# """폴더 멤버 관리 · 폴더 삭제."""

# from __future__ import annotations

# import streamlit as st

# from components.member_pills import ordered_host_first
# from config import CURRENT_USER_ID as ME
# from data import repository as repo
# from state import actions
# from state import selectors as sel
# from state.store import get_state
# from ui.avatar import avatar
# from ui.buttons import button, button_group
# from ui.html import markup
# from utils.text import esc
# from utils.browser import scroll_to_bottom

# def _add_members(fid: str, members: list[str]) -> None:
#     """팀을 고르고 → 그 팀에서 아직 멤버가 아닌 사람을 골라 추가."""
#     s = get_state()
#     teams = repo.teams()
#     team_id = s.member_team if s.member_team in teams else next(iter(teams))
#     with st.container(key="mem-add", gap="small"):
#         markup('<b style="font-size:13px">멤버 추가</b> <span class="hint">팀을 고르고 이름을 누르면 바로 멤버로 추가돼요</span>')
#         button_group([(tid, t.name) for tid, t in teams.items()], team_id,
#                      lambda v: actions.update(member_team=v), key="mem_team")
#         team = teams[team_id]
#         addable = [mb for mb in team.members if mb.id not in members]
#         if not addable:
#             markup(f'<p class="hint" style="font-size:13px">{esc(team.name)}은(는) 모두 이미 멤버예요.</p>')
#             return
#         widget_key = f"mem_pick_{fid}_{team_id}_{s.member_nonce}"
#         st.pills(
#             f"{team.name} 팀원",
#             options=[mb.id for mb in addable],
#             format_func=lambda pid: next(f"+ {mb.name} · {mb.role}" for mb in addable if mb.id == pid),
#             selection_mode="multi",
#             key=widget_key,
#             label_visibility="collapsed",
#             on_change=actions.add_picked_members,  # 누르는 즉시 추가 → 아래 목록에 바로 반영
#             args=(fid, widget_key),
#         )


# @st.dialog("폴더 멤버", width="medium", on_dismiss=actions.close_modal)
# def members_dialog() -> None:
#     s = get_state()
#     fid = s.folder_id
#     f = sel.folders(s)[fid]
#     members = sel.members_of(s, fid)
#     is_host = f.host == ME
#     markup(f'<h3 style="font-size:16px;font-weight:700">「{esc(f.name)}」 멤버 {len(members)}명</h3>'
#            '<p class="hint" style="font-size:13px;color:#5F646D">멤버만 이 폴더의 모든 회의록을 볼 수 있어요. 참석하지 않은 회차도 포함돼요.</p>')
#     if is_host:
#         _add_members(fid, members)

#     # 멤버 목록: 모달 안에서 이 영역만 스크롤, 사람 사이 구분선
#     with st.container(height=300, border=False, key="mem-list", gap=None):
#         for pid in ordered_host_first(f.host, members):
#             p = sel.person(pid)
#             host = pid == f.host
#             with st.container(key=f"memrow-{pid}", horizontal=True, vertical_alignment="center", gap="small"):
#                 markup(f'<div class="plist-row" style="border:none;padding:0">{avatar(p.initial, size=34, host=host)}'
#                        f'<span class="who"><b>{esc(p.name)}{" (나)" if pid == ME else ""}</b><span>{esc(p.team)}</span></span>'
#                        f'<span class="tag tag-{"host" if host else "att"}">{"폴더 주관" if host else "멤버"}</span></div>')
#                 if is_host and not host:
#                     button("", key=f"mem_rm_{pid}", variant="ghost", icon=":material/close:", help="폴더에서 빼기",
#                            on_click=actions.remove_member, args=(fid, pid))
#     if s.member_just_added:  # 방금 추가한 사람이 보이도록 목록 맨 아래로
#         scroll_to_bottom(".st-key-mem-list", nonce=str(s.member_nonce))
#         s.member_just_added = False
#     if is_host:
#         markup('<p class="hint">뺀 사람은 이 폴더의 회의록과, 그 회의록에서 만들어진 위키 내용을 더 이상 볼 수 없어요.</p>')
#     with st.container(horizontal=True, horizontal_alignment="right"):
#         if button("저장", key="mem_close", variant="dark"):
#             actions.close_modal()
#             st.rerun()


# @st.dialog("폴더 삭제", width="small", on_dismiss=actions.close_modal)
# def delete_folder_dialog() -> None:
#     s = get_state()
#     fid = s.folder_id
#     f = sel.folders(s)[fid]
#     markup(f'<h3 style="font-size:16px;font-weight:700">「{esc(f.name)}」 폴더를 삭제할까요?</h3>'
#            f'<p style="color:#3A3E45">회의록 {len(f.notes)}개가 함께 삭제되고, 멤버 {len(sel.members_of(s, fid))}명 모두 더 이상 볼 수 없어요. '
#            "위키와 외부 AI 연동에서도 빠져요. 되돌릴 수 없어요.</p>")
#     with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
#         if button("취소", key="del_cancel", variant="secondary"):
#             actions.close_modal()
#             st.rerun()
#         if button("삭제", key="del_ok", variant="danger"):
#             actions.delete_folder(fid)
#             st.rerun()

"""폴더 멤버 관리 · 폴더(프로젝트·기간) 삭제 · 회의 삭제."""

from __future__ import annotations

import streamlit as st

from components.member_pills import ordered_host_first
from config import CURRENT_USER_ID as ME
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button, button_group
from ui.html import markup
from utils.text import esc
from utils.browser import scroll_to_bottom

def _add_members(fid: str, members: list[str]) -> None:
    """팀을 고르고 → 그 팀에서 아직 멤버가 아닌 사람을 골라 추가."""
    s = get_state()
    teams = repo.teams()
    team_id = s.member_team if s.member_team in teams else next(iter(teams))
    with st.container(key="mem-add", gap="small"):
        markup('<b style="font-size:13px">멤버 추가</b> <span class="hint">팀을 고르고 이름을 누르면 바로 멤버로 추가돼요</span>')
        button_group([(tid, t.name) for tid, t in teams.items()], team_id,
                     lambda v: actions.update(member_team=v), key="mem_team")
        team = teams[team_id]
        addable = [mb for mb in team.members if mb.id not in members]
        if not addable:
            markup(f'<p class="hint" style="font-size:13px">{esc(team.name)}은(는) 모두 이미 멤버예요.</p>')
            return
        widget_key = f"mem_pick_{fid}_{team_id}_{s.member_nonce}"
        st.pills(
            f"{team.name} 팀원",
            options=[mb.id for mb in addable],
            format_func=lambda pid: next(f"+ {mb.name} · {mb.role}" for mb in addable if mb.id == pid),
            selection_mode="multi",
            key=widget_key,
            label_visibility="collapsed",
            on_change=actions.add_picked_members,  # 누르는 즉시 추가 → 아래 목록에 바로 반영
            args=(fid, widget_key),
        )


@st.dialog("폴더 멤버", width="medium", on_dismiss=actions.close_modal)
def members_dialog() -> None:
    s = get_state()
    fs = sel.folders(s)
    scope = [k for k in s.members_scope if k in fs]
    fid = s.members_folder if s.members_folder in fs else s.folder_id
    if len(scope) > 1:  # 기간 폴더: 그 안의 프로젝트 폴더를 골라 가며 관리
        markup('<span class="hint" style="font-size:13px">이 기간의 회의가 들어 있는 프로젝트 폴더예요 · 폴더를 골라 멤버를 관리하세요</span>')
        button_group([(k, fs[k].name) for k in scope], fid, lambda v: actions.update(members_folder=v),
                     key="mem_scope")
    f = fs[fid]
    members = sel.members_of(s, fid)
    is_host = f.host == ME
    markup(f'<h3 style="font-size:16px;font-weight:700">「{esc(f.name)}」 멤버 {len(members)}명</h3>'
           '<p class="hint" style="font-size:13px;color:#5F646D">멤버는 이 폴더에서 자기가 주관·참석한 회의록을 볼 수 있어요. 빠진 회차(조퇴·불참)는 주관자가 공유해 주면 보여요.</p>')
    if is_host:
        _add_members(fid, members)

    # 멤버 목록: 모달 안에서 이 영역만 스크롤, 사람 사이 구분선
    with st.container(height=300, border=False, key="mem-list", gap=None):
        for pid in ordered_host_first(f.host, members):
            p = sel.person(pid)
            host = pid == f.host
            with st.container(key=f"memrow-{pid}", horizontal=True, vertical_alignment="center", gap="small"):
                markup(f'<div class="plist-row" style="border:none;padding:0">{avatar(p.initial, size=34, host=host)}'
                       f'<span class="who"><b>{esc(p.name)}{" (나)" if pid == ME else ""}</b><span>{esc(p.team)}</span></span>'
                       f'<span class="tag tag-{"host" if host else "att"}">{"폴더 주관" if host else "멤버"}</span></div>')
                if is_host and not host:
                    button("", key=f"mem_rm_{pid}", variant="ghost", icon=":material/close:", help="폴더에서 빼기",
                           on_click=actions.remove_member, args=(fid, pid))
    if s.member_just_added:  # 방금 추가한 사람이 보이도록 목록 맨 아래로
        scroll_to_bottom(".st-key-mem-list", nonce=str(s.member_nonce))
        s.member_just_added = False
    if is_host:
        markup('<p class="hint">뺀 사람은 이 폴더의 회의록과, 그 회의록에서 만들어진 위키 내용을 더 이상 볼 수 없어요.</p>')
    with st.container(horizontal=True, horizontal_alignment="right"):
        if button("저장", key="mem_close", variant="dark"):
            actions.close_modal()
            st.rerun()


@st.dialog("폴더 삭제", width="small", on_dismiss=actions.close_modal)
def delete_folder_dialog() -> None:
    from state import archive

    s = get_state()
    target = s.delete_target or ("folder", s.folder_id)
    if target[0] == "period":
        _, mtype, key = target
        p = archive.period(s, mtype, key)
        ids = [i for i in (p.meeting_ids if p else ()) if repo.meeting(i).host == ME]
        markup(f'<h3 style="font-size:16px;font-weight:700">「{esc(archive.period_label(key))}」 폴더를 삭제할까요?</h3>'
               f'<p style="color:#3A3E45">이 기간에 내가 주관한 회의록 {len(ids)}개가 삭제되고, 참석자·공유받은 사람 모두 더 이상 볼 수 없어요. '
               "다른 사람이 주관한 회의는 그대로 남아요. 되돌릴 수 없어요.</p>")
        on_ok = lambda: actions.delete_period(mtype, key, ids)  # noqa: E731
    else:
        fid = target[1]
        f = sel.folders(s)[fid]
        markup(f'<h3 style="font-size:16px;font-weight:700">「{esc(f.name)}」 폴더를 삭제할까요?</h3>'
               f'<p style="color:#3A3E45">회의록 {len(f.notes)}개가 함께 삭제되고, 멤버 {len(sel.members_of(s, fid))}명 모두 더 이상 볼 수 없어요. '
               "위키와 외부 AI 연동에서도 빠져요. 되돌릴 수 없어요.</p>")
        on_ok = lambda: actions.delete_folder(fid)  # noqa: E731
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="del_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("삭제", key="del_ok", variant="danger"):
            on_ok()
            st.rerun()


@st.dialog("회의 삭제", width="small", on_dismiss=actions.close_modal)
def delete_meeting_dialog() -> None:
    s = get_state()
    mid = s.delete_target[1] if len(s.delete_target) > 1 else s.meeting_id
    m = repo.meeting(mid)
    markup(f'<h3 style="font-size:16px;font-weight:700">「{esc(m.title)}」 회의를 삭제할까요?</h3>'
           f'<p style="color:#3A3E45">회의록 원문과 정리된 요약·결정·할 일이 모두 지워지고, 참석자 {len(m.people)}명과 '
           "공유받은 사람 모두 더 이상 볼 수 없어요. 되돌릴 수 없어요.</p>")
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="mdel_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("회의 삭제", key="mdel_ok", variant="danger"):
            actions.delete_meeting(mid)
            st.rerun()
