"""새 회의록 작성 화면의 선택 모달: 폴더 · 주관자 · 참석자.

- 회의 종류 · 폴더: 회의 종류(월간·주간·일반) → 보기(월별·주차별·일별 / 프로젝트별) → 폴더
  (최근 5개, 없으면 이름으로 검색) 또는 「+ 새 폴더 만들기」 (프로젝트는 이름, 기간은 날짜로)
- 주관자: 팀을 고르면 그 팀 사람들이 나오고, 1명 선택
- 참석자: 팀을 고르면 그 팀 사람들이 나오고, 여러 명 선택 (다른 팀으로 바꿔도 고른 사람은 유지)
「선택」을 눌러야 작성 화면에 반영되고, 취소·X 로 닫으면 그대로입니다.
"""

from __future__ import annotations

import functools

from datetime import date

import streamlit as st

from components.org_people import org_people_picker
from config import CURRENT_USER_ID as ME
from data import repository as repo
from state import actions
from state import archive as arc
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button, button_group
from ui.html import markup
from utils.browser import ime_live
from utils.dates import date_key
from utils.text import esc, md_escape

_NEW = "new"


def _footer(kind: str, label: str, *, disabled: bool = False) -> None:
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key=f"{kind}_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button(label, key=f"{kind}_ok", variant="primary", disabled=disabled):
            actions.confirm_pick(kind)
            st.rerun()


# ================================================================ 회의 종류 · 폴더
_RECENT = 5  # 검색어가 없을 때 보여 줄 최근 폴더 수


def _project_options(s, mtype: str, query: str) -> list[str]:
    """프로젝트 폴더: 최근 회의가 있었던 순 (새로 만든 폴더 먼저) · 검색어가 있으면 이름으로 거름."""
    fids = sel.folder_ids_of_type(s, mtype)

    def recent(fid: str) -> tuple:
        notes = sel.folder_note_ids(s, fid)
        return (sel.folders(s)[fid].is_new, date_key(repo.meeting(notes[0]).date) if notes else 0)

    fids = sorted(fids, key=recent, reverse=True)
    if query:
        return [k for k in fids if query.lower() in sel.folders(s)[k].name.lower()]
    return fids[:_RECENT]


def _period_options(s, mtype: str, query: str) -> list[str]:
    """기간 폴더(주차·월·날): 최근 순 · 검색어가 있으면 이름(예: 10월)으로 거름."""
    keys = [p.key for p in arc.periods(s, mtype)]
    if query:
        return [k for k in keys if query.replace(" ", "") in arc.period_label(k).replace(" ", "")]
    return keys[:_RECENT]


def _new_period_key(s, mtype: str) -> str:
    """새 기간 폴더: 모달의 날짜(없으면 오늘)가 속한 주·달·날."""
    day = st.session_state.get(f"pick_pdate_{s.pick_nonce}") or date.today()
    return arc.period_key(mtype, f"{day.month}/{day.day}")


def _folder_button(s, t: str, g: str, opt: str, chosen_key: tuple, *, where: bool) -> None:
    """폴더 한 줄. where=True 면 '주간 회의 › 프로젝트별' 처럼 어디 있는 폴더인지도 보여 줌 (검색 결과용)."""
    types = repo.meeting_types()
    if g == "project":
        f = sel.folders(s)[opt]
        label = f.name
        meta = f"회의록 {len(sel.folder_note_ids(s, opt))}개 · 멤버 {len(sel.members_of(s, opt))}명" + (
            " · 내가 만든 폴더" if f.host == ME else "")
    else:
        p = arc.period(s, t, opt)
        label = arc.period_label(opt)
        meta = f"회의록 {len(p.meeting_ids)}개" if p else ""
    if where:
        meta = f"{types[t].name} › {arc.PERIOD_LABEL[t] if g == 'period' else '프로젝트별'} · {meta}"
    button(f"**{md_escape(label)}**  \n:gray[{meta}]", key=f"pf_{t}_{g}_{opt}",
           variant="pickon" if (t, g, opt) == chosen_key else "pick", on_click=actions.pick_folder_result,
           args=(t, g, opt), width="stretch", icon=":material/folder:")


def _search_results(s, query: str, mtype: str, group: str, chosen: str) -> None:
    """검색어가 있을 때: 회의 종류 · 보기와 상관없이 전체 폴더에서 이름으로 찾기."""
    found = [(t, "project", k) for t in sel.TYPE_ORDER for k in _project_options(s, t, query)]
    found += [(t, "period", k) for t in sel.TYPE_ORDER for k in _period_options(s, t, query)]
    markup(f'<div class="pick-section-head"><b>{esc(f"「{query}」 검색 결과 {len(found)}개")}</b>'
           '<span class="hint">모든 회의 종류에서 찾았어요 · 새 폴더는 검색어를 지우면 만들 수 있어요</span></div>')
    with st.container(key="pick-folders", gap="small"):
        if not found:
            markup('<p class="hint" style="font-size:13px;padding:8px 2px">찾는 폴더가 없어요. 검색어를 지우고 새로 만들어 보세요.</p>')
        for t, g, opt in found:
            _folder_button(s, t, g, opt, (mtype, group, chosen), where=True)


def _browse(s, mtype: str, group: str, chosen: str) -> None:
    """검색어가 없을 때: 회의 종류 → 보기 → 최근 폴더 5개."""
    types = repo.meeting_types()
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        markup('<span class="inline-label pick-step">회의 종류</span>')
        button_group([(k, types[k].name) for k in ("monthly", "weekly", "normal")], mtype, actions.set_pick_type,
                     key="pk_type", style="seg", gap=None)
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        markup('<span class="inline-label pick-step">폴더 보기</span>')
        button_group([("period", arc.PERIOD_LABEL[mtype]), ("project", "프로젝트별")], group, actions.set_pick_group,
                     key="pk_group", style="seg", gap=None)
    options = _project_options(s, mtype, "") if group == "project" else _period_options(s, mtype, "")
    markup(f'<div class="pick-section-head"><b>최근 폴더 {len(options)}개</b>'
           '<span class="hint">찾는 폴더가 없으면 위에서 검색하거나 새로 만드세요</span></div>')
    with st.container(key="pick-folders", gap="small"):
        if not options:
            markup('<p class="hint" style="font-size:13px;padding:8px 2px">아직 폴더가 없어요. 아래에서 새로 만들 수 있어요.</p>')
        for opt in options:
            _folder_button(s, mtype, group, opt, (mtype, group, chosen), where=False)


@st.dialog("회의 종류 · 폴더 선택", width="medium", on_dismiss=actions.close_modal)
def folder_picker_dialog() -> None:
    s = get_state()
    types = repo.meeting_types()
    mtype = s.pick_type if s.pick_type in types else "weekly"
    group = s.pick_group if s.pick_group in arc.GROUPS else "project"
    folders = sel.folders(s)

    # 모달 크기 고정: 버튼(취소·선택)을 뺀 내용은 이 높이 안에서만 스크롤
    with st.container(height=480, border=False, key="pick-folder-body", gap="small"):
        # 0) 맨 위 검색: 회의 종류와 상관없이 전체 폴더(프로젝트 · 주차/월/일)에서 찾기
        query = (st.text_input("폴더 찾기", key=f"pick_q_{s.pick_nonce}", label_visibility="collapsed",
                               placeholder="전체 폴더에서 찾기 (예: 릴리즈, 10월)", icon=":material/search:",
                               live=True) or "").strip()
        ime_live("폴더 찾기")  # 한글도 치는 대로 바로
        chosen = (s.pick_folder if s.pick_folder in folders or s.pick_folder == _NEW else "") if group == "project" \
            else s.pick_period
        if query:
            _search_results(s, query, mtype, group, chosen)
        else:
            _browse(s, mtype, group, chosen)

        # 4) 새 폴더 만들기 (고른 회의 종류 · 보기 안에) · 검색 중에는 숨김
        new_on = chosen == _NEW and not query
        where = f"{types[mtype].name} › {arc.PERIOD_LABEL[mtype] if group == 'period' else '프로젝트별'}"
        if not query:
            button(f"**+ 새 폴더 만들기**  \n:gray[{where} 안에 새로 만들어요]", key=f"pf_{group}_new",
                   variant="pickon" if new_on else "pick", width="stretch", icon=":material/create_new_folder:",
                   on_click=actions.update, kwargs={"pick_folder" if group == "project" else "pick_period": _NEW})
        if new_on and group == "project":
            st.text_input("새 폴더 이름", key=f"pick_name_{s.pick_nonce}", value=s.pick_name or query,
                          placeholder="이번 회의의 큰 주제 (예: 정산 자동화)")
        elif new_on:
            st.date_input("이 날짜가 속한 기간으로 폴더를 만들어요", value=date.today(), key=f"pick_pdate_{s.pick_nonce}",
                          format="YYYY/MM/DD")
            key = _new_period_key(s, mtype)
            same = arc.period(s, mtype, key) is not None
            markup(f'<span class="cp-hint">「{esc(arc.period_label(key))}」 폴더'
                   + ("는 이미 있어요 · 그 폴더에 올라가요" if same else "가 새로 만들어져요") + "</span>")

    if st.session_state.get("_pick_folder_err") == s.pick_nonce:
        markup('<span role="alert" style="font-size:13px;color:#B3261E">'
               + ("새 폴더 이름을 적어 주세요." if new_on else "폴더를 골라 주세요.") + "</span>")
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="cp_folder_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("선택", key="cp_folder_ok", variant="primary"):
            s.pick_type, s.pick_group = mtype, group
            if group == "project":
                # 엔터 없이 바로 「선택」을 눌러도 적은 이름이 들어가도록 입력칸 값을 직접 읽음
                s.pick_name = (st.session_state.get(f"pick_name_{s.pick_nonce}") or "") if new_on else ""
                ok = bool(chosen) and chosen != _NEW or bool(new_on and s.pick_name.strip())
                s.pick_folder = chosen
            else:
                s.pick_period = _new_period_key(s, mtype) if new_on else chosen
                ok = bool(s.pick_period) and s.pick_period != _NEW
            if not ok:
                st.session_state["_pick_folder_err"] = s.pick_nonce  # 이번에 연 모달에서만 안내
                st.rerun(scope="fragment")
            actions.confirm_pick("cp_folder")
            st.rerun()


# ================================================================ 주관자 · 참석자
def _person_option(pid: str) -> str:
    p = sel.person(pid)
    role = sel.person_role(pid)
    return f"{p.name}" + (f" · {role}" if role else "") + (" (나)" if pid == ME else "")


# 「선택한 참석자」 상자 스크롤 (한 번만 설치되는 브라우저 스크립트)
# - 사람을 누를 때마다 상자가 새로 그려지는데, 그 즉시(화면에 그리기 전) 이전 스크롤 위치를 되돌려 놓아 삐끗거리지 않음
# - 칩이 새 줄로 넘어갈 때(줄 수가 늘 때)만 맨 아래로 내려 방금 고른 사람을 보여 줌
# - 모달을 새로 열면 맨 아래부터
_CHOSEN_SCROLL_JS = """<script>
(function () {
  if (window.__pickChosenScroll) return;
  window.__pickChosenScroll = true;
  var mem = {top: null, rows: 0};
  function rowsOf(box) {
    var tops = {};
    box.querySelectorAll('.pick-chip').forEach(function (c) { tops[c.offsetTop] = 1; });
    return Object.keys(tops).length;
  }
  function apply(box) {
    if (box.__sig === box.dataset.n) return;
    box.__sig = box.dataset.n;
    var rows = rowsOf(box);
    if (mem.top === null || rows > mem.rows) box.scrollTop = box.scrollHeight;
    else box.scrollTop = mem.top;
    mem.rows = rows;
    mem.top = box.scrollTop;
    if (!box.__listen) {
      box.__listen = true;
      box.addEventListener('scroll', function () { mem.top = box.scrollTop; });
    }
  }
  new MutationObserver(function () {
    var boxes = document.querySelectorAll('.pick-chosen');
    if (!boxes.length) { mem.top = null; mem.rows = 0; return; }
    boxes.forEach(apply);
  }).observe(document.body, {childList: true, subtree: true, characterData: true});
  document.querySelectorAll('.pick-chosen').forEach(apply);
})();
</script>"""


def _people_picker(kind: str, *, single: bool) -> None:
    s = get_state()

    # 지금 고른 사람: 제목은 상자 위에 고정, 사람 칩만 상자 안에서 스크롤
    title = ("연결할 사람" if kind == "cp_speaker" else "주관자") if single else f"선택한 참석자 {len(s.pick_draft)}명"
    if s.pick_draft:
        chips = "".join(
            f'<span class="pick-chip">{avatar(sel.person(pid).initial, size=22, host=single)}'
            f'{esc(sel.person(pid).name)}<span class="t">{esc(sel.person(pid).team)}</span></span>'
            for pid in s.pick_draft
        )
    else:
        chips = '<span class="pick-empty">아직 고른 사람이 없어요</span>'
    # 높이가 고정된 상자 · 넘치면 상자 안에서 스크롤. 스크롤 위치는 _CHOSEN_SCROLL_JS 가 관리
    markup(f'<div class="pick-chosen-head">{esc(title)}</div>'
           f'<div class="pick-chosen" data-n="{len(s.pick_draft)}"><div class="pick-chosen-in">{chips}</div></div>')
    if not single:
        with st.container(key="pick-js"):  # 화면에 자리를 차지하지 않는 숨은 칸
            st.html(_CHOSEN_SCROLL_JS, unsafe_allow_javascript=True)

    # 사람 고르기: 조직도(본부 › 부 › 팀 › 사람) · 검색 · 최근 · 즐겨찾기 (팀 · 조직이 많아도 찾기 쉽게)
    hint = ("이름을 누르면 이 발화자로 연결돼요" if kind == "cp_speaker" else "이름을 누르면 주관자로 골라져요") if single \
        else "이름을 누르면 선택 · 다시 누르면 해제"
    markup(f'<div class="pick-section-head"><b>사람 찾기</b><span class="hint">{esc(hint)}</span></div>')
    org_people_picker(kind, chosen=list(s.pick_draft), single=single,
                      on_pick=functools.partial(actions.pick_person, single=single), height=280)


@st.dialog("주관자 선택", width="medium", on_dismiss=actions.close_modal)
def host_picker_dialog() -> None:
    _people_picker("cp_host", single=True)
    _footer("cp_host", "선택", disabled=not get_state().pick_draft)


@st.dialog("참석자 선택", width="medium", on_dismiss=actions.close_modal)
def people_picker_dialog() -> None:
    n = len(get_state().pick_draft)
    _people_picker("cp_people", single=False)
    _footer("cp_people", f"{n}명 선택" if n else "선택 안 함")


@st.dialog("발화자 연결", width="medium", on_dismiss=actions.close_modal)
def speaker_picker_dialog() -> None:
    """2단계: 임시 발화자(참석자1 · A)를 실제 사람과 연결. 팀을 고르면 팀원이 나와요. 모르면 미지정."""
    s = get_state()
    markup(f'<p class="hint" style="font-size:14px;color:#2B2F36">원문의 <b>「{esc(s.pick_label)}」</b>은(는) 누구인가요? '
           "팀을 고르고 이름을 누르세요. 모르면 「미지정으로 두기」를 누르면 돼요.</p>")
    _people_picker("cp_speaker", single=True)
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="cp_speaker_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("미지정으로 두기", key="cp_speaker_none", variant="secondary"):
            s.pick_draft = []
            actions.confirm_pick("cp_speaker")
            st.rerun()
        if button("연결하기", key="cp_speaker_ok", variant="primary", disabled=not s.pick_draft):
            actions.confirm_pick("cp_speaker")
            st.rerun()
