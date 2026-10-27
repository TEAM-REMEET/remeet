"""검토 화면에서 쓰는 모달(다이얼로그)."""
from __future__ import annotations

import streamlit as st

from core import auth, router
from core.state import flash, get_meeting, members
from data.constants import ACCESS_LABEL, PRIORITIES, REVIEW_LABEL, WORK_LABEL
from services import delivery, review
from services.rules import member_name, recommend, warnings_of
from ui.components import badge, history_line, html, kv_list, phase, source_tag, warn_chips
from ui.doc_preview import meeting_doc
from utils.dates import dday, dday_label, fmt_d, to_date
from utils.text import esc, sid


def _who() -> str:
    return auth.me()["name"]


# ---------------------------------------------------------------- 액션 아이템 상세

def _reco_body(m: dict, a: dict, can: bool) -> None:
    """담당자 추천 카드 + 직접 지정."""
    st.caption("AI 추천은 제안이에요. 선택해야 담당자로 지정되고, 원문에 근거가 없으면 미지정으로 남겨 둘 수 있어요.")
    rec = recommend(a, m, members())
    if rec["hold"]:
        st.info(rec["hold"])
    else:
        cols = st.columns(len(rec["items"]))
        for i, (col, c) in enumerate(zip(cols, rec["items"])):
            mem = c["member"]
            with col, st.container(key=f"panel_reco_{mem['id']}"):
                html(f'<div class="fw6">{i + 1}. {esc(mem["name"])} <span class="c-accent" style="float:right">{c["score"]}점</span></div>'
                     f'<div class="m-sub">{esc(mem["dept"])} · {esc(mem["role"])}{"" if c["attendee"] else " · 비참석자"}</div>')
                html('<div class="bars">' + "".join(
                    f'<div class="bar-row"><span>{label}</span><i><b style="width:{v}%"></b></i><em>{v}</em></div>'
                    for label, v in (("스킬", c["skill"]), ("부서·역할", c["role"]), ("업무 여유", c["ease"]))) + "</div>"
                     f'<div class="tiny muted" style="margin-top:6px;line-height:1.5">{esc(" · ".join(c["reasons"]))}</div>')
                if can and st.button("지정", key=f"pick_{mem['id']}", type="primary" if i == 0 else "secondary", use_container_width=True):
                    reopened = review.assign(m, a["id"], mem["id"], "suggested", members(), _who())
                    flash(f"{mem['name']} 님을 담당자로 지정했어요." + (" 승인했던 항목이라 다시 검토 전으로 돌아갔어요." if reopened else ""))
                    st.rerun()
    if can:
        options = [x["id"] for x in members() if not x.get("no_recommend")]
        c1, c2, c3 = st.columns([3, 1.3, 1.3], vertical_alignment="bottom")
        pick = c1.selectbox("구성원 검색으로 직접 지정", [None] + options, format_func=lambda mid: "이름, 부서로 검색" if mid is None else
                            next(f"{x['name']} · {x['dept']} · 진행 {x['load']}건" for x in members() if x["id"] == mid))
        if c2.button("지정", key="pick_manual", disabled=pick is None, use_container_width=True):
            reopened = review.assign(m, a["id"], pick, "manual", members(), _who())
            flash("담당자를 지정했어요." + (" 승인했던 항목이라 다시 검토 전으로 돌아갔어요." if reopened else ""))
            st.rerun()
        if not a["assignee"] and c3.button("미지정 유지", use_container_width=True):
            review.keep_unassigned(m, a["id"], _who())
            flash("미지정으로 남겨 두었어요.")
            st.rerun()
    st.caption("점수 = 스킬 적합도 40% + 부서·역할 40% + 업무 여유도 20%. 이번 회의에서 이미 맡은 업무도 반영해요.")


def _form_body(m: dict, a: dict | None) -> None:
    """액션 추가·수정 입력 폼."""
    d = m["detail"]
    topics = [t["id"] for t in d["topics"]]
    k = a["id"] if a else "new"
    title = st.text_input("업무 내용", value=a["title"] if a else "", placeholder="예) 웰컴 쿠폰 예산 출처 확인", key=f"f_title_{k}")
    assignee = None
    if not a:
        assignee = st.selectbox("담당자", [None] + [x["id"] for x in members() if not x.get("no_recommend")], key=f"f_who_{k}",
                                format_func=lambda mid: "미지정" if mid is None else member_name(members(), mid))
    c1, c2, c3 = st.columns([1.4, 1, 1], vertical_alignment="bottom")
    due = c1.date_input("기한", value=to_date(a["due"]) if a and a["due"] else to_date("2026-10-30"), key=f"f_due_{k}", format="YYYY.MM.DD")
    no_due = c2.checkbox("기한 미정", value=bool(a and not a["due"]), key=f"f_nodue_{k}")
    priority = c3.selectbox("우선순위", PRIORITIES, index=PRIORITIES.index(a["priority"]) if a else 1, key=f"f_pri_{k}")
    topic = st.selectbox("관련 안건", topics, index=topics.index(a["topic"]) if a else 0, key=f"f_topic_{k}",
                         format_func=lambda tid: f"안건 {review.topic_no(d, tid)} · " + next(t["title"] for t in d["topics"] if t["id"] == tid))
    if a and a["due_raw"]:
        st.caption(f"원문 표현: “{a['due_raw']}”")
    if a and a["review"] == "approved":
        st.caption("승인한 항목이라 내용을 바꾸면 다시 ‘검토 전’이 돼요.")
    if st.button("저장" if a else "추가", type="primary", use_container_width=True, key=f"f_save_{k}"):
        if not title.strip():
            st.error("업무 내용을 입력해 주세요.")
            return
        form = {"title": title, "due": None if no_due else due.isoformat(), "priority": priority, "topic": topic, "assignee": assignee}
        if a:
            changed, reopened = review.edit_action(m, a["id"], form, _who())
            flash("바뀐 내용이 없어요." if not changed else "수정했어요. 다시 검토 전으로 돌아갔어요." if reopened else "수정했어요.")
        else:
            review.add_action(m, form, _who())
            flash("액션 아이템을 추가했어요.")
        st.rerun()


def _set_review(m: dict, action_id: str, value: str, message: str) -> None:
    review.set_review(m, action_id, value, _who())
    flash(message)
    st.rerun()


@st.dialog("액션 아이템", width="large")
def action_detail_dialog(meeting_id: str, action_id: str) -> None:
    m = get_meeting(meeting_id)
    d = m["detail"]
    a = review.find_action(d, action_id)
    can = review.can_edit(m, auth.me()["id"])
    tracking = review.is_tracking(m)
    no = review.action_no(d, action_id)

    state = badge(WORK_LABEL[a["work"]], "run") if tracking else badge(REVIEW_LABEL[a["review"]], {"draft": "wait", "approved": "ok", "rejected": "neutral"}[a["review"]])
    html(f'<div class="small muted">액션 {no} · 안건 {review.topic_no(d, a["topic"])}</div><div style="font-size:18px;font-weight:700;margin:4px 0">{state} {esc(a["title"])}</div>')
    due = (fmt_d(a["due"], True) + f" · {dday_label(dday(a['due']))}") if a["due"] else "미정"
    if a["due_raw"]:
        due += f' <span class="muted">(원문 “{esc(a["due_raw"])}”{" · 추정" if a["estimated"] else ""})</span>'
    kv_list([("담당자", f'{esc(member_name(members(), a["assignee"]))} {source_tag(a["source"])}'), ("기한", due), ("우선순위", esc(a["priority"]))])
    warns = warnings_of(a, members()) if not tracking and a["review"] != "rejected" else []
    if warns:
        html(warn_chips(warns))
    evidence = [s for s in d["segments"] if s["id"] in a["refs"]]
    if evidence:
        html('<div class="sub-h" style="margin-top:6px">원문 근거</div><div class="quote">' + "".join(f'<p><b>{sid(s["id"])}</b>{esc(s["text"])}</p>' for s in evidence) + "</div>")
    else:
        st.caption("직접 추가한 업무라 원문 근거가 없어요.")

    if tracking:
        if a["review"] == "approved":
            editable = auth.is_owner(m) or a["assignee"] == auth.me()["id"]
            c1, c2 = st.columns([3, 2], vertical_alignment="center")
            value = c1.segmented_control("진행 상태", list(WORK_LABEL), default=a["work"], format_func=WORK_LABEL.get, disabled=not editable, key=f"work_{a['id']}")
            if value and value != a["work"]:
                review.set_work(m, a["id"], value, _who())
                flash(f"진행 상태를 ‘{WORK_LABEL[value]}’(으)로 바꿨어요.")
                st.rerun()
            c2.toggle("기한 리마인더", value=a["remind"], key=f"rm_{a['id']}", on_change=review.toggle_remind, args=(m, a["id"]))
        return
    if not can:
        st.caption("열람 전용이에요. 결과 수정과 승인은 주관자만 할 수 있어요.")
        return

    with st.container(horizontal=True):
        if a["review"] == "rejected":
            if st.button("되돌리기", key="d_undo"):
                _set_review(m, a["id"], "draft", "검토 전으로 되돌렸어요.")
        else:
            if a["review"] != "approved" and st.button("승인", key="d_ok", type="primary", icon=":material/check:"):
                _set_review(m, a["id"], "approved", f"액션 {no}을 승인했어요.")
            if a["review"] == "approved" and st.button("승인 취소", key="d_unok"):
                _set_review(m, a["id"], "draft", "승인을 취소했어요.")
            if st.button("거절", key="d_no"):
                _set_review(m, a["id"], "rejected", f"액션 {no}을 거절했어요. 저장·전달에서 빠져요.")
    if a["review"] != "rejected":
        t1, t2 = st.tabs(["담당자 지정", "내용 수정"])
        with t1:
            _reco_body(m, a, can)
        with t2:
            _form_body(m, a)


@st.dialog("액션 아이템 추가")
def add_action_dialog(meeting_id: str) -> None:
    _form_body(get_meeting(meeting_id), None)


@st.dialog("요약 편집", width="large")
def summary_dialog(meeting_id: str) -> None:
    m = get_meeting(meeting_id)
    d = m["detail"]
    core = st.text_area("핵심 요약", value=d["core"], height=100)
    st.markdown("**안건별 논의**")
    notes = [st.text_area(f"안건 {i + 1} · {t['title']}", value=t["note"], height=80, key=f"sum_t{i}") for i, t in enumerate(d["topics"])]
    st.markdown("**결정사항**")
    decisions = [st.text_area(f"결정 {i + 1}", value=x["text"], height=68, key=f"sum_d{i}") for i, x in enumerate(d["decisions"])]
    st.caption("고친 문장에는 ‘수정됨’ 표시가 붙고 변경 이력에 남아요.")
    if st.button("저장", type="primary", use_container_width=True):
        flash("요약을 저장했어요." if review.save_summary(m, core, notes, decisions, _who()) else "바뀐 내용이 없어요.")
        st.rerun()


@st.dialog("다음 회의 안건 편집")
def agenda_dialog(meeting_id: str) -> None:
    m = get_meeting(meeting_id)
    text = st.text_area("한 줄에 안건 하나씩 적어 주세요", value="\n".join(x["text"] for x in m["detail"]["agenda"]), height=200)
    if st.button("저장", type="primary", use_container_width=True):
        review.save_agenda(m, text.splitlines(), _who())
        flash("다음 회의 안건을 저장했어요.")
        st.rerun()


def _close_footer(key: str, note: str = "", extra=None) -> None:
    """프로토타입 모달 아래쪽: 왼쪽 안내 문구 + 오른쪽 닫기(+추가 버튼)."""
    with st.container(key=f"mfoot_{key}", horizontal=True, vertical_alignment="center"):
        html(f'<span class="small muted">{note}</span>', width="content")
        with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
            if st.button("닫기", key=f"close_{key}"):
                st.rerun()
            if extra:
                extra()


def _set_access(meeting_id: str) -> None:
    m = get_meeting(meeting_id)
    value = st.session_state[f"access_{meeting_id}"]
    if value != m["access"]:
        review.set_access(m, value, _who())
        st.toast("공개 범위를 바꿨어요.", icon="✅")


@st.dialog("회의 정보")
def info_dialog(meeting_id: str) -> None:
    m = get_meeting(meeting_id)
    nm = lambda mid: esc(member_name(members(), mid))  # noqa: E731
    ref = f' · 참고 자료 {esc(m["ref_file"])}' if m.get("ref_file") else ""
    html('<dl class="kv-list info-kv">'
         f'<div><dt>회의 일시</dt><dd>{fmt_d(m["date"], True)} {m["start"]} – {m["end"]}</dd></div>'
         f'<div><dt>주관자</dt><dd>{nm(m["organizer"])} · {esc(m["dept"])}</dd></div>'
         f'<div><dt>참석자</dt><dd>{", ".join(nm(x) for x in m["attendees"])}</dd></div>'
         f'<div><dt>회의록</dt><dd>{esc(m["file"])}{ref}</dd></div>'
         f'<div><dt>분석 완료</dt><dd>{esc(m.get("analyzed_at") or "—")}</dd></div></dl>')
    keys = list(ACCESS_LABEL)
    with st.container(key="info_access", horizontal=True, vertical_alignment="center", gap="small"):
        html('<span class="kv-dt">공개 범위</span>', width="content")
        st.selectbox("공개 범위", keys, index=keys.index(m["access"]), format_func=ACCESS_LABEL.get, key=f"access_{meeting_id}",
                     disabled=not auth.is_owner(m), label_visibility="collapsed", width=140, on_change=_set_access, args=(meeting_id,))
        html(phase(2), width="content")
    _close_footer("info")


@st.dialog("변경 이력")
def history_dialog(meeting_id: str) -> None:
    html('<p class="small muted" style="margin:0 0 4px">AI 초안과 사람의 수정, 승인 기록을 모두 남겨요.</p>')
    rows = []
    for h in get_meeting(meeting_id)["detail"]["history"]:
        diffs = "".join(f'<div class="diff">{esc(c["f"])}: <s>{esc(c["b"])}</s> → <b>{esc(c["a"])}</b></div>' for c in h.get("changes", []))
        rows.append(f'<div class="hist"><span class="muted">{esc(h["t"])}</span><div><strong>{esc(h["who"])}</strong> · {esc(h["text"])}{diffs}</div></div>')
    with st.container(key="hist_body"):
        html("".join(rows) or '<p class="small muted">아직 기록이 없어요.</p>')
    _close_footer("history")


@st.dialog("결과 내보내기", width="large")
def export_dialog(meeting_id: str) -> None:
    m = get_meeting(meeting_id)
    fmt = st.segmented_control("형식", ["Markdown", "JSON"], default="Markdown", label_visibility="collapsed", key="export_fmt") or "Markdown"
    if fmt == "Markdown":
        body, name, mime = delivery.build_markdown(m, members()), f"{m['id']}.md", "text/markdown"
    else:
        body, name, mime = delivery.payload_json(m, members(), "export"), f"{m['id']}.json", "application/json"
    st.code(body, language="markdown" if fmt == "Markdown" else "json", wrap_lines=True, height=380)

    def download():
        st.download_button("파일 받기", body, file_name=name, mime=mime, type="primary", icon=":material/download:", key="export_dl")

    _close_footer("export", "오른쪽 위 복사 아이콘으로 복사하거나 파일로 받을 수 있어요.", download)


@st.dialog("전체 미리보기", width="large")
def preview_dialog(meeting_id: str) -> None:
    m = get_meeting(meeting_id)
    sent = review.is_sent(m)
    with st.container(key="pv_body"):  # 문서만 안에서 스크롤, 아래 안내·닫기는 고정
        meeting_doc(m, members(), include_drafts=not sent, key="pv_doc")
    with st.container(key="pv_foot", horizontal=True, vertical_alignment="center"):
        html('<span class="small muted">' + ("외부로 전달된 내용이에요." if sent else
             "지금 상태로 확정·전달하면 이 내용이 Slack·메일·Notion 등으로 전달돼요.") + "</span>")
        if st.button("닫기", key="pv_close"):
            st.rerun()


@st.dialog("결과를 확정할까요?")
def confirm_dialog(meeting_id: str) -> None:
    m = get_meeting(meeting_id)
    c = review.counts(m["detail"])
    if c["draft"]:
        st.warning(f"검토 전 항목이 {len(c['draft'])}건 있어요. 모든 액션 아이템을 승인 또는 거절해야 확정할 수 있어요.")
        if st.button("검토 전 항목 모두 승인", use_container_width=True):
            n = review.approve_all_drafts(m, _who())
            flash(f"검토 전 {n}건을 승인했어요.")
            st.rerun()
        return
    per: dict[str, int] = {}
    for a in c["approved"]:
        if a["assignee"]:
            per[a["assignee"]] = per.get(a["assignee"], 0) + 1
    unassigned = sum(1 for a in c["approved"] if not a["assignee"])
    d = m["detail"]
    st.markdown("확정하면 아래 내용이 반영돼요.")
    st.markdown(
        f"1. **확정 데이터로 저장 (버전 {(m.get('version') or 0) + 1})** — 요약 · 결정사항 {len(d['decisions'])}건 · 승인한 액션 {len(c['approved'])}건 · 미해결 이슈 {len(d['issues'])}건 · 다음 안건 {len(d['agenda'])}건\n"
        f"2. **구성원 업무 이력에 반영** — " + (" · ".join(f"{member_name(members(), k)} +{v}" for k, v in per.items()) or "담당자가 정해진 업무가 없어요") +
        (f" · 담당자 미정 {unassigned}건은 반영되지 않아요" if unassigned else "") + "\n"
        f"3. **제외** — 거절한 액션 {len(c['rejected'])}건은 저장·전달·이력에 들어가지 않아요\n"
        "4. **외부 전달은 아직 안 돼요** — 다음 화면에서 대상과 미리보기를 확인한 뒤 따로 승인해요. 보내기 전까지는 계속 수정할 수 있어요."
    )
    if any(a["edited"] or a["manual"] for a in d["actions"]) or c["rejected"]:
        st.info("액션 아이템을 수정·거절했다면 요약과 결정사항이 맞는지 확인해 주세요. 요약은 자동으로 바뀌지 않아요.")
    c1, c2 = st.columns(2)
    if c1.button("취소", use_container_width=True):
        st.rerun()
    if c2.button("확정", type="primary", use_container_width=True):
        result = review.confirm(m, members(), _who())
        flash(f"버전 {result['version']}으로 확정했어요. 외부로 보내기 전까지는 계속 수정할 수 있어요.")
        router.go_now("delivery", meeting_id=meeting_id)
