"""분석 결과 검토 · 승인 · 확정.

왼쪽에는 회의 원문(다운로드 · 키워드 검색), 오른쪽에는 탭(요약 / 액션 아이템 / 미해결 이슈 / 다음 회의 안건)을 둡니다.
오른쪽 항목을 누르면 왼쪽 원문에서 근거 문단이 강조되고, 액션 아이템은 상세 모달이 열립니다.
"""
from __future__ import annotations

import time

import streamlit as st

from core import auth, router
from core.state import flash, get_meeting, meetings, members
from data.constants import WORK_LABEL
from services import review
from services.rules import member_name, warnings_of
from ui import visuals
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


# ---------------------------------------------------------------- 왼쪽: 원문 (다운로드 · 키워드 검색)

def _q_key(meeting_id: str) -> str:
    return f"src_q_{meeting_id}"


def _i_key(meeting_id: str) -> str:
    return f"src_i_{meeting_id}"


def _on_query(meeting_id: str) -> None:
    st.session_state[_i_key(meeting_id)] = 0
    st.session_state["src_jump"] = True


def _step(meeting_id: str, delta: int, n: int) -> None:
    st.session_state[_i_key(meeting_id)] = (st.session_state.get(_i_key(meeting_id), 0) + delta) % n
    st.session_state["src_jump"] = True


def _mark(text: str, q: str) -> str:
    return esc(text).replace(esc(q), f"<mark>{esc(q)}</mark>") if q else esc(text)


def _source_text(m: dict) -> str:
    d = m["detail"]
    out = [f"{m['title']}", f"{m['date']} {m['start']}–{m['end']} · {m['file']}", ""]
    for i, t in enumerate(d["topics"], 1):
        out.append(f"[안건 {i}] {t['title']}")
        out += [f"{sid(s['id'])} {s['text']}" for s in d["segments"] if t["from"] <= s["id"] <= t["to"]]
        out.append("")
    return "\n".join(out)


def _source_panel(m: dict) -> None:
    d = m["detail"]
    sel = _selected(m["id"])
    refs = set(sel["refs"]) if sel else set()
    q = st.session_state.get(_q_key(m["id"]), "").strip()
    order = [s for t in d["topics"] for s in d["segments"] if t["from"] <= s["id"] <= t["to"]]
    hits = [s["id"] for s in order if q and q in s["text"]]
    idx = st.session_state.get(_i_key(m["id"]), 0) % len(hits) if hits else 0
    cur = hits[idx] if hits else None
    with panel("src", flush=True):
        with st.container(key="src_head"):
            with st.container(horizontal=True, vertical_alignment="center"):
                html(f'<div class="tt" style="font-size:16px;font-weight:700">원문</div>'
                     f'<div class="small muted" style="margin-top:2px">{esc(m["file"])} · 문단 {len(d["segments"])}개</div>')
                with st.container(key="sm_src_dl", width="content"):
                    st.download_button("다운로드", _source_text(m), file_name=f"{m['id']}_원문.txt", mime="text/plain",
                                       icon=":material/download:", key="src_download")
            with st.container(key="src_search", horizontal=True, vertical_alignment="center", gap="small"):
                st.text_input("원문 검색", key=_q_key(m["id"]), placeholder="발언 내용에서 키워드 검색", label_visibility="collapsed",
                              icon=":material/search:", on_change=_on_query, args=(m["id"],))
                if q:
                    html(f'<span class="small {"muted" if not hits else "c-accent fw6"}">{f"{idx + 1} / {len(hits)}" if hits else "결과 없음"}</span>', width="content")
                    st.button("‹", key="src_prev", disabled=not hits, on_click=_step, args=(m["id"], -1, max(len(hits), 1)), help="이전 결과")
                    st.button("›", key="src_next", disabled=not hits, on_click=_step, args=(m["id"], 1, max(len(hits), 1)), help="다음 결과")
        lines = []
        for i, t in enumerate(d["topics"], 1):
            lines.append(f'<div class="src-topic" style="text-align:right"><span>안건 {i}</span>{esc(t["title"])}</div>')
            for s in d["segments"]:
                if t["from"] <= s["id"] <= t["to"]:
                    cls = "ln" + (" hl" if s["id"] in refs else "") + (" cur" if s["id"] == cur else "")
                    lines.append(f'<div class="{cls}"><span class="n">{sid(s["id"])}</span><span>{_mark(s["text"], q)}</span></div>')
        with st.container(key="src_scroll"):
            html("".join(lines))
        if st.session_state.pop("src_jump", False) and cur:
            _scroll_to(".st-key-src_scroll .ln.cur", f"q{cur}")
        elif sel:
            _scroll_to(".st-key-src_scroll .ln.hl", f"{sel['id']}{sel['refs']}")
        with st.container(key="src_foot", horizontal=True, vertical_alignment="center"):
            if sel:
                html(f'<span class="small c-accent fw6">📍 {esc(sel["label"])}</span>')
                st.button("강조 해제", key="hl_clear", type="tertiary", on_click=_clear, args=(m["id"],))
            else:
                html('<span class="small muted">오른쪽 항목을 누르면 근거 문단이 표시돼요.</span>')


def _scroll_to(selector: str, tag_: str) -> None:
    """원문 영역 안에서 해당 문단이 보이도록 스크롤합니다(Streamlit에는 스크롤 API가 없어 작은 스크립트를 씁니다)."""
    with st.container(key="src_js"):
        st.iframe(f"""<script>/* {esc(tag_)} {time.time()} */
setTimeout(() => {{
  const el = window.parent.document.querySelector('{selector}');
  if (!el) return;
  let box = el.parentElement;
  while (box && box.scrollHeight <= box.clientHeight + 2) box = box.parentElement;
  if (box) box.scrollTo({{top: el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 90, behavior: 'smooth'}});
}}, 150);
</script>""", height=1)


def _scroll_page_to(selector: str, tag_: str) -> None:
    """오른쪽 탭 영역에서 해당 항목이 화면 가운데 오도록 스크롤합니다."""
    with st.container(key="rv_js"):
        st.iframe(f"""<script>/* {esc(tag_)} {time.time()} */
setTimeout(() => {{
  const el = window.parent.document.querySelector('{selector}');
  if (el) el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
}}, 250);
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


def _doc_item(m: dict, item_id: str, no: int, title: str, body: str, refs: list[int], label: str, edited: bool = False, when: str = "") -> None:
    sel = _selected(m["id"])
    on = sel and sel["id"] == item_id
    with tap(f"{'docsel' if on else 'doc'}_{item_id}", _select, (m["id"], item_id, refs, label), label=f"{label} 근거 보기"):
        mark = f' <span class="edited">수정됨</span>' if edited else ""
        mark += f' <span class="di-when">{when}</span>' if when else ""
        html(f'<div class="di"><span class="n">{no}</span><div><div class="t">{esc(title)}{mark}</div>'
             + (f'<div class="p">{esc(body)}</div>' if body else "") + "</div></div>")


# ---------------------------------------------------------------- 탭: 요약

def _pick_topic(meeting_id: str, topic: dict) -> None:
    """논의 비중 막대를 누르면: 왼쪽 원문 구간 강조 + 아래 ‘안건별 논의’의 그 안건으로 이동."""
    _select(meeting_id, topic["id"], list(range(topic["from"], topic["to"] + 1)), f"안건 {topic['no']} · {topic['title']}")
    st.session_state["rv_scroll_item"] = topic["id"]


def _count_strip(m: dict) -> None:
    d = m["detail"]
    cells = [("안건", len(d["topics"]), ""), ("결정사항", len(d["decisions"]), ""),
             ("액션 아이템", len([a for a in d["actions"] if a["review"] != "rejected"]), ""),
             ("미해결 이슈", len(d["issues"]), "c-err" if d["issues"] else ""), ("회의 시간", f'{visuals.meeting_minutes(m)}<small>분</small>', "")]
    html('<div class="cnt-strip">' + "".join(f'<div><span>{k}</span><b class="{c}">{v}</b></div>' for k, v, c in cells) + "</div>")


def _tab_summary(m: dict, can: bool) -> None:
    d = m["detail"]
    times = visuals.topic_times(m)

    def edit_button():
        if can:
            with st.container(key="sm_edit_summary", width="content"):
                if st.button("요약 편집", icon=":material/edit:", key="edit_summary"):
                    dlg.summary_dialog(m["id"])

    _tab_bar(m, "summary", edit_button)
    with st.container(key="tabbody_summary"):
        _count_strip(m)
        html(f'<div class="sub-h" style="padding-top:14px">회의 개요</div><p class="doc-lead" style="margin-top:8px">{esc(d["core"])}'
             + (' <span class="edited">수정됨</span>' if d["core_edited"] else "") + f'</p><div class="small muted">목적 · {esc(d["purpose"])}</div>')
        if times:
            longest = max(t["min"] for t in times)
            html('<div class="sub-h" style="padding-top:14px;border-top:1px solid var(--line-2)">안건별 논의 비중 '
                 f'<span class="small muted" style="font-weight:400;margin-left:4px">많이 논의한 순 · {visuals.meeting_minutes(m)}분 기준 · 누르면 아래 내용으로 이동</span></div>')
            with st.container(key="share_list"):
                for t in sorted(times, key=lambda t: t["rank"]):
                    with tap(f"share_{t['id']}", _pick_topic, (m["id"], t), label=f"안건 {t['no']} · {t['title']} 내용 보기"):
                        html(visuals.share_bar_html(t, longest))
        mins = {t["id"]: t for t in times}
        html('<div class="sub-h" style="padding-top:14px;border-top:1px solid var(--line-2)">안건별 논의</div>')
        for i, t in enumerate(d["topics"], 1):
            when = f'{mins[t["id"]]["start"]}–{mins[t["id"]]["end"]} · {round(mins[t["id"]]["min"])}분' if t["id"] in mins else ""
            _doc_item(m, t["id"], i, t["title"], t["note"], list(range(t["from"], t["to"] + 1)), f"안건 {i} · {t['title']}", t["edited"], when)
        html('<div class="sub-h" style="padding-top:14px;border-top:1px solid var(--line-2)">결정사항</div>')
        if not d["decisions"]:
            st.caption("결정된 내용이 없어요.")
        for i, x in enumerate(d["decisions"], 1):
            _doc_item(m, x["id"], i, x["text"], "", x["refs"], f"결정 {i}", x["edited"])
    target = st.session_state.pop("rv_scroll_item", None)   # 비중 막대에서 고른 안건으로 스크롤
    if target:
        _scroll_page_to(f".st-key-tap_docsel_{target}", target)


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
            # 이슈 내용 + 아래 태그/버튼을 한 칸으로 묶어, 마우스를 올리면 칸 전체가 회색이 돼요
            with st.container(key=f"issuebox_{x['id']}"):
                _doc_item(m, x["id"], i, x["title"], x["text"], x["refs"], f"이슈 {i} · {x['title']}")
                with st.container(horizontal=True, key=f"issue_act_{x['id']}"):
                    if x["to_next"]:
                        html(tag("다음 회의 안건에 추가됨", "ai"), width="content")
                    elif can:
                        st.button("다음 회의 안건에 추가 ›", key=f"toag_{x['id']}", type="tertiary",
                                  on_click=review.issue_to_agenda, args=(m, x["id"], auth.me()["name"]))


def _flow(m: dict) -> None:
    """회의 흐름: 이전 회의(키워드가 겹치는 앞선 회의) → 이번 회의 → 다음 회의."""
    d = m["detail"]
    prev, common = visuals.previous_meeting(m, [x for x in meetings() if auth.can_view(x)])
    carried = sum(1 for x in d["issues"] if x.get("to_next"))
    html('<div class="sub-h">회의 흐름 <span class="small muted" style="font-weight:400;margin-left:4px">이 주제가 어떻게 이어지는지</span></div>')
    with st.container(key="flow", horizontal=True, vertical_alignment="center", gap="small"):
        if prev:
            with tap(f"flow_prev_{prev['id']}", router.go, ("review",), {"meeting_id": prev["id"]}, label=f"이전 회의 {prev['title']} 열기"):
                html(f'<div class="fl-k">이전 회의 · {fmt_d(prev["date"])}</div><div class="fl-t">{esc(prev["title"])}</div>'
                     f'<div class="fl-s">겹치는 말: {esc(", ".join(common[:3]))}</div>')
        else:
            html('<div class="fl-box fl-empty"><div class="fl-k">이전 회의</div><div class="fl-s">이어지는 회의를 찾지 못했어요</div></div>')
        html('<span class="fl-arrow">→</span>', width="content")
        html(f'<div class="fl-box fl-now"><div class="fl-k">이번 회의 · {fmt_d(m["date"])}</div><div class="fl-t">{esc(m["title"])}</div>'
             f'<div class="fl-s">결정 {len(d["decisions"])} · 미해결 {len(d["issues"])}</div></div>')
        html('<span class="fl-arrow">→</span>', width="content")
        html(f'<div class="fl-box fl-next"><div class="fl-k">다음 회의 · 날짜 미정</div><div class="fl-t">안건 {len(d["agenda"])}개 준비됨</div>'
             f'<div class="fl-s">{f"미해결 이슈 {carried}건 넘어감" if carried else "넘어가는 미해결 이슈 없음"}</div></div>')


def _tab_agenda(m: dict, can: bool) -> None:
    d = m["detail"]

    def edit_button():
        if can:
            with st.container(key="sm_edit_agenda", width="content"):
                if st.button("안건 편집", icon=":material/edit:", key="edit_agenda"):
                    dlg.agenda_dialog(m["id"])

    _tab_bar(m, "agenda", edit_button)
    with st.container(key="tabbody_agenda"):
        _flow(m)
        html('<div class="sub-h" style="padding-top:14px;border-top:1px solid var(--line-2)">다음 회의 안건 '
             '<span class="small muted" style="font-weight:400;margin-left:4px">캘린더로 전달하면 일정 설명란에 들어가요</span></div>')
        if not d["agenda"]:
            st.caption("등록된 안건이 없어요.")
        for i, x in enumerate(d["agenda"], 1):
            src = '<span class="ag-src warn">미해결 이슈</span>' if x.get("issue") else '<span class="ag-src">회의에서 정함</span>'
            html(f'<div class="di ag-row"><span class="n">{i}</span><div class="t">{esc(x["text"])}</div>{src}</div>')


# ---------------------------------------------------------------- 화면

def _attendees(m: dict) -> None:
    """제목 아래 참석자 칩 (주관자가 맨 앞)."""
    ids = [m["organizer"]] + [x for x in m["attendees"] if x != m["organizer"]]
    chips = []
    for mid in ids:
        name = member_name(members(), mid)
        org = mid == m["organizer"]
        chips.append(f'<span class="att{" org" if org else ""}" title="{esc(name)}"><span class="av">{esc(name[:1])}</span>{esc(name)}'
                     + ('<em>주관</em>' if org else "") + "</span>")
    html(f'<div class="att-row"><span class="small muted">참석 {len(m["attendees"])}명</span>{"".join(chips)}</div>')


def _notices(m: dict) -> None:
    if review.is_sent(m):
        notice("🔒 <strong>외부로 전달된 회의예요.</strong> 결과는 더 이상 수정할 수 없고, 업무 진행 상태와 리마인더만 바꿀 수 있어요.")
    elif not auth.is_owner(m):
        notice(f"🔒 열람 전용이에요. 결과 수정과 확정은 주관자({esc(member_name(members(), m['organizer']))} 님)만 할 수 있어요.")
    elif m.get("reconfirm"):
        notice("⚠ <strong>확정 후 내용이 바뀌었어요.</strong> 다시 확정해야 바뀐 내용으로 전달할 수 있어요.", "warn")
    elif m["status"] == "CONFIRMED":
        notice(f"버전 {m['version']}으로 확정했어요. 외부로 보내기 전까지는 계속 수정할 수 있고, 수정하면 다시 확정해야 해요.", "ok")


def _open_more(name: str) -> None:
    st.session_state["rv_more_open"] = name
    st.session_state["rv_more"] = False  # 더보기 메뉴를 닫고 모달을 엽니다


def _head_actions(m: dict, can: bool) -> None:
    with st.container(key="rv_more_box", width="content"), st.popover("더보기", key="rv_more", on_change="rerun"):
        with st.container(key="more_menu"):
            for label, name in (("회의 정보 · 공개 범위", "info"), ("변경 이력", "history"), ("내보내기 (Markdown · JSON)", "export")):
                st.button(label, key=f"more_{name}", type="tertiary", on_click=_open_more, args=(name,))
    opened = st.session_state.pop("rv_more_open", None)
    if opened:
        {"info": dlg.info_dialog, "history": dlg.history_dialog, "export": dlg.export_dialog}[opened](m["id"])
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
    _attendees(m)
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