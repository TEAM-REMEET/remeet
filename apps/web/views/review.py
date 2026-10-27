"""분석 결과 검토 · 승인 · 확정.

왼쪽에는 회의 원문, 오른쪽에는 탭(요약 / 액션 아이템 / 미해결 이슈 / 다음 회의 안건)을 둡니다.
오른쪽 항목을 누르면 왼쪽 원문에서 근거 문단이 강조되고, 액션 아이템은 상세 모달이 열립니다.
"""
from __future__ import annotations

import time

import streamlit as st

from core import auth, router
from core.state import flash, get_meeting, members
from data.constants import WORK_LABEL
from services import review
from services.rules import member_name, warnings_of
from ui.components import badge, html, notice, status_badge, tag
from ui.layout import head_row, page_head, panel, tap
from utils.dates import fmt_d
from utils.text import esc, sid
from views import review_dialogs as dlg

TABS = ["요약", "액션 아이템", "미해결 이슈", "다음 회의 안건"]


# ---------------------------------------------------------------- 선택 상태 (원문 강조 · 열 모달)

def _sel_key(meeting_id: str) -> str:
    return f"rv_sel_{meeting_id}"


def _selected(meeting_id: str) -> dict | None:
    return st.session_state.get(_sel_key(meeting_id))


def _select(meeting_id: str, item_id: str, refs: list[int], label: str, open_action: bool = False) -> None:
    st.session_state[_sel_key(meeting_id)] = {"id": item_id, "refs": list(refs), "label": label}
    if open_action:
        st.session_state["rv_open_action"] = item_id


def _clear(meeting_id: str) -> None:
    st.session_state.pop(_sel_key(meeting_id), None)


# ---------------------------------------------------------------- 왼쪽: 원문

def _source_panel(m: dict) -> None:
    d = m["detail"]
    sel = _selected(m["id"])
    refs = set(sel["refs"]) if sel else set()
    with panel("src", flush=True):
        html(f'<div style="padding:18px 20px 12px;border-bottom:1px solid var(--line-2)"><div class="tt" style="font-size:16px;font-weight:700">원문</div>'
             f'<div class="small muted" style="margin-top:4px">{esc(m["file"])} · 문단 {len(d["segments"])}개</div></div>')
        lines = []
        for i, t in enumerate(d["topics"], 1):
            lines.append(f'<div class="src-topic" style="text-align:right"><span>안건 {i}</span>{esc(t["title"])}</div>')
            for s in d["segments"]:
                if t["from"] <= s["id"] <= t["to"]:
                    lines.append(f'<div class="{"ln hl" if s["id"] in refs else "ln"}"><span class="n">{sid(s["id"])}</span><span>{esc(s["text"])}</span></div>')
        with st.container(key="src_scroll"):
            html("".join(lines))
        if sel:
            _scroll_to_highlight(sel)
        with st.container(key="src_foot", horizontal=True, vertical_alignment="center"):
            if sel:
                html(f'<span class="small c-accent fw6">📍 {esc(sel["label"])}</span>')
                st.button("강조 해제", key="hl_clear", type="tertiary", on_click=_clear, args=(m["id"],))
            else:
                html('<span class="small muted">오른쪽 항목을 누르면 근거 문단이 표시돼요.</span>')


def _scroll_to_highlight(sel: dict) -> None:
    """강조된 첫 문단이 보이도록 원문 영역을 스크롤합니다(Streamlit에는 스크롤 API가 없어 작은 스크립트를 씁니다)."""
    with st.container(key="src_js"):
        st.iframe(f"""<script>/* {esc(sel['id'])} {sel['refs']} {time.time()} */
setTimeout(() => {{
  const el = window.parent.document.querySelector('.st-key-src_scroll .ln.hl');
  if (!el) return;
  let box = el.parentElement;
  while (box && box.scrollHeight <= box.clientHeight + 2) box = box.parentElement;
  if (box) box.scrollTo({{top: el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 90, behavior: 'auto'}});
}}, 150);
</script>""", height=1)


# ---------------------------------------------------------------- 오른쪽: 탭 공통

def _tab_bar(m: dict, name: str, extra=None) -> None:
    c = review.counts(m["detail"])
    warn_n = sum(1 for a in m["detail"]["actions"] if a["review"] != "rejected" and warnings_of(a, members()))
    if review.is_sent(m):
        state = "<strong>전달 완료</strong> · 업무 진행 상태를 관리해요"
    elif m["status"] == "CONFIRMED":
        state = f"<strong>확정 · 버전 {m['version']}</strong> · 보내기 전까지 수정할 수 있어요"
    else:
        total = len([a for a in m["detail"]["actions"]])
        state = f"<strong>AI 초안</strong> · 검토 {total - len(c['draft'])}/{total} · 확인 필요 {warn_n}건"
    with st.container(key=f"tabbar_{name}", horizontal=True, vertical_alignment="center"):
        html(f'<span class="small" style="color:var(--fg-3)">{state}</span>')
        if extra:
            extra()
        with st.container(key=f"line_sm_preview_{name}", width="content"):
            if st.button("전체 미리보기", icon=":material/description:", key=f"open_preview_{name}"):
                dlg.preview_dialog(m["id"])


def _doc_item(m: dict, item_id: str, no: int, title: str, body: str, refs: list[int], label: str, edited: bool = False) -> None:
    sel = _selected(m["id"])
    on = sel and sel["id"] == item_id
    with tap(f"{'docsel' if on else 'doc'}_{item_id}", _select, (m["id"], item_id, refs, label), label=f"{label} 근거 보기"):
        mark = f' <span class="edited">수정됨</span>' if edited else ""
        html(f'<div class="di"><span class="n">{no}</span><div><div class="t">{esc(title)}{mark}</div>'
             + (f'<div class="p">{esc(body)}</div>' if body else "") + "</div></div>")


# ---------------------------------------------------------------- 탭: 요약

def _tab_summary(m: dict, can: bool) -> None:
    d = m["detail"]

    def edit_button():
        if can:
            with st.container(key="sm_edit_summary", width="content"):
                if st.button("요약 편집", icon=":material/edit:", key="edit_summary"):
                    dlg.summary_dialog(m["id"])

    _tab_bar(m, "summary", edit_button)
    with st.container(key="tabbody_summary"):
        html(f'<div class="sub-h">회의 개요</div><p class="doc-lead" style="margin-top:8px">{esc(d["core"])}'
             + (' <span class="edited">수정됨</span>' if d["core_edited"] else "") + f'</p><div class="small muted">목적 · {esc(d["purpose"])}</div>')
        html('<div class="sub-h" style="padding-top:14px;border-top:1px solid var(--line-2)">안건별 논의</div>')
        for i, t in enumerate(d["topics"], 1):
            _doc_item(m, t["id"], i, t["title"], t["note"], list(range(t["from"], t["to"] + 1)), f"안건 {i} · {t['title']}", t["edited"])
        html('<div class="sub-h" style="padding-top:14px;border-top:1px solid var(--line-2)">결정사항</div>')
        if not d["decisions"]:
            st.caption("결정된 내용이 없어요.")
        for i, x in enumerate(d["decisions"], 1):
            _doc_item(m, x["id"], i, x["text"], "", x["refs"], f"결정 {i}", x["edited"])


# ---------------------------------------------------------------- 탭: 액션 아이템

def _action_row(m: dict, a: dict, tracking: bool) -> None:
    d = m["detail"]
    no = review.action_no(d, a["id"])
    sel = _selected(m["id"])
    on = (sel and sel["id"] == a["id"]) or router.arg("focus") == a["id"]
    warns = [] if tracking or a["review"] == "rejected" else warnings_of(a, members())
    icon = {"approved": '<span class="ic ok">✓</span>', "rejected": '<span class="ic no">✕</span>'}.get(a["review"], '<span class="ic"></span>')
    if tracking:
        icon = '<span class="ic ok">✓</span>' if a["work"] == "done" else '<span class="ic"></span>'
    title = f"<s>{esc(a['title'])}</s>" if a["review"] == "rejected" else esc(a["title"])
    meta = [esc(member_name(members(), a["assignee"], "")) if a["assignee"] else '<span class="w">담당자 미정</span>',
            fmt_d(a["due"]) if a["due"] else '<span class="w">기한 미정</span>', f"우선순위 {esc(a['priority'])}"]
    if tracking:
        st_text, st_cls = WORK_LABEL[a["work"]], "ok" if a["work"] == "done" else ""
    elif a["review"] == "approved":
        st_text, st_cls = "승인", "ok"
    elif a["review"] == "rejected":
        st_text, st_cls = "거절", ""
    else:
        st_text, st_cls = ("확인 필요", "warn") if warns else ("검토 전", "")
    with tap(f"{'actsel' if on else 'act'}_{a['id']}", _select, (m["id"], a["id"], a["refs"], f"액션 {no} · {a['title']}", True), label=f"액션 {no} 열기"):
        html(f'<div class="act">{icon}<div><div class="t">{title}</div><div class="m">{" · ".join(meta)}</div></div>'
             f'<span class="st {st_cls}">{st_text}</span></div>')


def _tab_actions(m: dict, can: bool) -> None:
    d = m["detail"]
    tracking = review.is_tracking(m)
    c = review.counts(d)
    _tab_bar(m, "actions")
    with st.container(key="tabbody_actions"):
        acts = [a for a in d["actions"] if a["review"] == "approved"] if tracking else d["actions"]
        if not tracking:
            needs = [a for a in d["actions"] if a["review"] == "draft" and warnings_of(a, members())]
            filters = {"all": f"전체 {len(d['actions'])}", "draft": f"검토 전 {len(c['draft'])}", "warn": f"확인 필요 {len(needs)}"}
            with st.container(horizontal=True, vertical_alignment="center"):
                pick = st.segmented_control("보기", list(filters), default="all", format_func=filters.get, key=f"act_filter_{m['id']}", label_visibility="collapsed") or "all"
                if can:
                    with st.container(horizontal=True, horizontal_alignment="right"), st.container(key="sm_add_action", width="content"):
                        if st.button("추가", icon=":material/add:", key="add_action"):
                            dlg.add_action_dialog(m["id"])
            if pick == "draft":
                acts = c["draft"]
            elif pick == "warn":
                acts = needs
        if not acts:
            st.caption("해당하는 업무가 없어요.")
        for a in acts:
            _action_row(m, a, tracking)
        if can and not tracking:
            with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
                html(f'<span class="small muted">검토 전 {len(c["draft"])}건</span>', width="content")
                if st.button("남은 항목 모두 승인", key="approve_all", type="tertiary", disabled=not c["draft"]):
                    flash(f"{review.approve_all_drafts(m, auth.me()['name'])}건을 승인했어요.")
                    st.rerun()


# ---------------------------------------------------------------- 탭: 미해결 이슈 · 다음 회의 안건

def _tab_issues(m: dict, can: bool) -> None:
    d = m["detail"]
    _tab_bar(m, "issues")
    with st.container(key="tabbody_issues"):
        if not d["issues"]:
            st.caption("미해결 이슈가 없어요.")
        for i, x in enumerate(d["issues"], 1):
            _doc_item(m, x["id"], i, x["title"], x["text"], x["refs"], f"이슈 {i} · {x['title']}")
            with st.container(horizontal=True, key=f"issue_act_{x['id']}"):
                if x["to_next"]:
                    html(tag("다음 회의 안건에 추가됨", "ai"), width="content")
                elif can:
                    st.button("다음 회의 안건에 추가 ›", key=f"toag_{x['id']}", type="tertiary",
                              on_click=review.issue_to_agenda, args=(m, x["id"], auth.me()["name"]))


def _tab_agenda(m: dict, can: bool) -> None:
    d = m["detail"]

    def edit_button():
        if can:
            with st.container(key="sm_edit_agenda", width="content"):
                if st.button("안건 편집", icon=":material/edit:", key="edit_agenda"):
                    dlg.agenda_dialog(m["id"])

    _tab_bar(m, "agenda", edit_button)
    with st.container(key="tabbody_agenda"):
        st.caption("다음 회의에서 이어서 다룰 안건이에요. 캘린더로 전달하면 일정 설명란에 들어가요.")
        if not d["agenda"]:
            st.caption("등록된 안건이 없어요.")
        for i, x in enumerate(d["agenda"], 1):
            html(f'<div class="di" style="padding:12px 10px;border-bottom:1px solid var(--line-2)"><span class="n">{i}</span><div><div class="t">{esc(x["text"])}</div>'
                 + ('<div class="small muted">미해결 이슈에서 추가</div>' if x.get("issue") else "") + "</div></div>")


# ---------------------------------------------------------------- 화면

def _notices(m: dict) -> None:
    if review.is_sent(m):
        notice("🔒 <strong>외부로 전달된 회의예요.</strong> 결과는 더 이상 수정할 수 없고, 업무 진행 상태와 리마인더만 바꿀 수 있어요.")
    elif not auth.is_owner(m):
        notice(f"🔒 열람 전용이에요. 결과 수정과 확정은 주관자({esc(member_name(members(), m['organizer']))} 님)만 할 수 있어요.")
    elif m.get("reconfirm"):
        notice("⚠ <strong>확정 후 내용이 바뀌었어요.</strong> 다시 확정해야 바뀐 내용으로 전달할 수 있어요.", "warn")
    elif m["status"] == "CONFIRMED":
        notice(f"버전 {m['version']}으로 확정했어요. 외부로 보내기 전까지는 계속 수정할 수 있고, 수정하면 다시 확정해야 해요.", "ok")


def _head_actions(m: dict, can: bool) -> None:
    with st.popover("더보기", key="rv_more"):
        if st.button("회의 정보 · 공개 범위", key="more_info", type="tertiary"):
            dlg.info_dialog(m["id"])
        if st.button("변경 이력", key="more_history", type="tertiary"):
            dlg.history_dialog(m["id"])
        if st.button("결과 내보내기 (MD · JSON)", key="more_export", type="tertiary"):
            dlg.export_dialog(m["id"])
    if can and m["status"] == "REVIEW_PENDING":
        if st.button("다시 확정하기" if m.get("reconfirm") else "확정하기", type="primary", icon=":material/check:", key="open_confirm"):
            dlg.confirm_dialog(m["id"])
    elif auth.is_owner(m) and m["status"] == "CONFIRMED":
        st.button("전달하기", type="primary", icon=":material/send:", on_click=router.go, args=("delivery",), kwargs={"meeting_id": m["id"]})
    elif m["status"] in ("SYNCED", "SYNC_PARTIAL"):
        st.button("전달 현황", on_click=router.go, args=("delivery",), kwargs={"meeting_id": m["id"]})


def render() -> None:
    m = get_meeting(router.arg("meeting_id"))
    if not m or not auth.can_view(m) or not m.get("detail"):
        st.warning("결과를 볼 수 없는 회의예요. 삭제되었거나, 열람 권한이 없거나, 아직 분석 중이에요.")
        st.button("회의 목록으로", on_click=router.go, args=("meetings",))
        return
    d = m["detail"]
    can = review.can_edit(m, auth.me()["id"])

    left, actions = head_row()
    with left:
        page_head(m["title"], f"{fmt_d(m['date'], True)} {m['start']}–{m['end']} · 주관 {member_name(members(), m['organizer'])} · 참석 {len(m['attendees'])}명",
                  ["홈", "회의", "결과 검토"], status_badge(m["status"]) + (f" {badge('v' + str(m['version']), 'neutral')}" if m.get("version") else ""))
    with actions:
        _head_actions(m, can)
    _notices(m)

    # 목록·알림에서 특정 업무를 열고 들어온 경우 액션 탭을 먼저 보여줍니다.
    focus = router.arg("focus")
    default_tab = TABS[1] if focus else TABS[0]
    if focus and st.session_state.get("_rv_focus_done") != id(router.current()):
        st.session_state._rv_focus_done = id(router.current())
        st.session_state.pop(f"rv_tabs_{m['id']}", None)
        a = next((x for x in d["actions"] if x["id"] == focus), None)
        if a:
            _select(m["id"], a["id"], a["refs"], f"액션 {review.action_no(d, a['id'])} · {a['title']}")

    src, right = st.columns([5, 6.6], gap="medium")
    with src:
        _source_panel(m)
    with right, panel("tabs", flush=True):
        n_act = len([a for a in d["actions"] if a["review"] != "rejected"])
        labels = {TABS[0]: TABS[0], TABS[1]: f"{TABS[1]} {n_act}", TABS[2]: f"{TABS[2]} {len(d['issues'])}", TABS[3]: f"{TABS[3]} {len(d['agenda'])}"}
        tabs = st.tabs(list(labels.values()), default=labels[default_tab], key=f"rv_tabs_{m['id']}")
        with tabs[0]:
            _tab_summary(m, can)
        with tabs[1]:
            _tab_actions(m, can)
        with tabs[2]:
            _tab_issues(m, can)
        with tabs[3]:
            _tab_agenda(m, can)

    opened = st.session_state.pop("rv_open_action", None)
    if opened and any(a["id"] == opened for a in d["actions"]):
        dlg.action_detail_dialog(m["id"], opened)