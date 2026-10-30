"""확정 결과 전달: ① 대상·항목 선택 → ② 미리보기 → ③ 전송 승인 → ④ 전송 결과."""
from __future__ import annotations

import streamlit as st

from core import auth, router
from core.state import flash, get_meeting, integrations, members
from data.constants import DELIVERY_LABEL, INTEG_STATUS
from services import delivery
from ui.components import badge, html, notice, phase, status_badge
from ui.doc_preview import target_name, target_preview
from ui.layout import head_row, page_head, panel, panel_head
from utils.dates import fmt_d
from utils.text import esc
from views import review_dialogs

STEPS = ["대상·항목 선택", "미리보기", "전송 승인", "전송 결과"]
CHANNELS = ["#온보딩-tf", "#서비스기획", "#전사-공지"]
RESULT_GRID = [1.2, 5, 1.3, 1.5, 1.3]


# ---------------------------------------------------------------- 상태값

def _key(meeting_id: str, name: str) -> str:
    return f"dv_{name}_{meeting_id}"


def _options(meeting_id: str) -> dict:
    ss = st.session_state
    extra = [x.strip() for x in ss.get(_key(meeting_id, "extra"), "").split(",") if x.strip()]
    return {"channel": ss.get(_key(meeting_id, "channel"), CHANNELS[0]), "extra": extra, "project": "ONB"}


def _items(meeting_id: str) -> dict:
    return {k: st.session_state.get(_key(meeting_id, f"item_{k}"), True) for k in delivery.ITEM_LABEL}


def _targets(meeting_id: str) -> list[str]:
    return [t for t in delivery.TARGET_ORDER if st.session_state.get(_key(meeting_id, f"t_{t}"))]


def _reconnect(target: str) -> None:
    integrations()[target]["status"] = "connected"
    if not integrations()[target]["account"]:
        integrations()[target]["account"] = "사내 계정"
    flash(f"{target_name(target)}을(를) 연결했어요.")


def _send(m: dict, targets: list[str]) -> None:
    delivery.send(m, targets, integrations(), _options(m["id"]))
    failed = [t for t in targets if not delivery.already_sent(m, t)]
    if failed:
        flash(f"{', '.join(target_name(t) for t in failed)} 전달에 실패했어요. 연결을 확인한 뒤 다시 보내 주세요.", "⚠️")
    else:
        flash(f"{len(targets)}곳으로 전달했어요.")
    for k in ("agree", "previewed"):
        st.session_state.pop(_key(m["id"], k), None)

    for k in ("agree", "previewed"):
        st.session_state.pop(_key(m["id"], k), None)
    if router.arg("mode") == "more":  # 추가 전송을 마치면 전송 결과 화면으로
        router.go("delivery", meeting_id=m["id"])


# ---------------------------------------------------------------- 공통 조각

def _stepper(current: int, done_upto: int) -> None:
    cells = []
    for i, name in enumerate(STEPS):
        on = i <= done_upto if current < 3 else i == 3
        cells.append(f'<span class="{"on" if on else ""}"><b>{i + 1}</b>{name}</span>')
    with panel("dv_steps"):
        html('<div class="steps" style="justify-content:flex-start;gap:18px">' + "".join(cells) + "</div>")


@st.dialog("전달 미리보기", width="large")
def _preview_dialog(meeting_id: str, targets: list[str]) -> None:
    m = get_meeting(meeting_id)
    tabs = st.tabs([target_name(t) for t in targets] + ["전송 데이터(JSON)"])
    for tab, t in zip(tabs, targets):
        with tab:
            target_preview(t, m, members(), _items(meeting_id), _options(meeting_id))
    with tabs[-1]:
        st.caption("대상마다 같은 형식의 데이터를 보내요. 멱등 키로 같은 버전이 두 번 전송되지 않게 막아요.")
        st.code(delivery.payload_json(m, members(), targets[0], _items(meeting_id)), language="json")


# ---------------------------------------------------------------- ①~③ 선택 · 미리보기 · 승인

def _target_card(m: dict, t: str) -> None:
    info = integrations()[t]
    label, kind = INTEG_STATUS[info["status"]]
    sent = delivery.already_sent(m, t)
    key = _key(m["id"], f"t_{t}")
    on = st.session_state[key]
    with st.container(key=f"{'tcardon' if on else 'tcard'}_{t}"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            p2 = f" {phase(2)}" if info["phase"] == 2 else ""
            st.checkbox(f"**{info['name']}** :gray[{info['desc']}]", key=key, disabled=sent or info["status"] != "connected")
            if p2:
                html(p2, width="content")
            with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
                html(badge("전송 완료", "ok") if sent else badge(label, kind), width="content")
                if not sent and info["status"] == "expired":
                    with st.container(key=f"line_rc_{t}", width="content"):
                        st.button("다시 연결", key=f"rc_{t}", on_click=_reconnect, args=(t,))
                elif not sent and info["status"] == "disconnected":
                    with st.container(key=f"sm_cn_{t}", width="content"):
                        st.button("연결하기", key=f"cn_{t}", on_click=_reconnect, args=(t,))
        if not on:
            return
        with st.container(key=f"topt_{t}", horizontal=True, vertical_alignment="center", gap="small"):
            if t == "slack":
                html('<span class="small muted">채널</span>', width="content")
                st.selectbox("채널", CHANNELS, key=_key(m["id"], "channel"), label_visibility="collapsed", width=180)
            elif t == "email":
                html(f'<span class="small muted">참석자 {len(m["attendees"])}명 · 추가 수신자</span>', width="content")
                st.text_input("추가 수신자", key=_key(m["id"], "extra"), placeholder="이메일, 쉼표로 구분", label_visibility="collapsed", width=240)
            elif t == "calendar":
                html(f'<span class="small muted">다음 회의 {fmt_d(delivery.next_meeting_date(m))} {m["start"]} · 참석자 {len(m["attendees"])}명 초대</span>')
            elif t == "jira":
                html(f'<span class="small muted">액션 아이템 {len(delivery.approved_actions(m["detail"]))}건을 ONB 프로젝트 이슈로 만들어요</span>')
            else:
                html('<span class="small muted">전체 미리보기와 같은 형식의 페이지로 만들어져요</span>')


def _init_targets(m: dict) -> None:
    """처음 들어왔을 때 연결된 기본 대상(Slack·이메일·캘린더)을 골라 둡니다."""
    for t in delivery.TARGET_ORDER:
        key = _key(m["id"], f"t_{t}")
        if key not in st.session_state:
            info = integrations()[t]
            st.session_state[key] = t in ("slack", "email", "calendar") and info["status"] == "connected" and not delivery.already_sent(m, t)
        elif integrations()[t]["status"] != "connected":
            st.session_state[key] = False


def _select_form(m: dict) -> None:
    d = m["detail"]
    _init_targets(m)
    targets = _targets(m["id"])
    previewed = st.session_state.get(_key(m["id"], "previewed"), False)
    agreed = st.session_state.get(_key(m["id"], "agree"), False)
    _stepper(0, 2 if agreed else 1 if previewed or targets else 0)

    left, right = st.columns([2.75, 1], gap="medium")
    with left:
        with panel("dv_targets"):
            with st.container(horizontal=True, vertical_alignment="center"):
                panel_head("전달 대상")
                st.button("연동 설정 ›", key="go_integ", type="tertiary", on_click=router.go, args=("integrations",))
            for t in delivery.TARGET_ORDER:
                _target_card(m, t)
        with panel("dv_items"):
            panel_head("전달 항목")
            counts = {"summary": "", "decisions": f" {len(d['decisions'])}건", "actions": f" {len(delivery.approved_actions(d))}건 (승인된 항목)",
                      "issues": f" {len(d['issues'])}건", "agenda": f" {len(d['agenda'])}건"}
            labels = {"summary": "회의 개요 · 안건별 논의", "decisions": "결정사항", "actions": "액션 아이템", "issues": "미해결 이슈", "agenda": "다음 회의 안건"}
            cols = st.columns(3)
            for i, k in enumerate(delivery.ITEM_LABEL):
                cols[i % 3].checkbox(labels[k] + counts[k], key=_key(m["id"], f"item_{k}"), value=True)

    with right, panel("dv_approve"):
        panel_head("전송 승인")
        n_items = sum(_items(m["id"]).values())
        html('<dl class="kv-line">'
             f'<div><dt>보낼 대상</dt><dd>{esc(", ".join(target_name(t) for t in targets) or "선택 안 함")}</dd></div>'
             f'<div><dt>보낼 항목</dt><dd>{n_items}개</dd></div></dl>')
        if st.button("미리보기 확인하기", use_container_width=True, disabled=not targets, key="open_preview"):
            st.session_state[_key(m["id"], "previewed")] = True
            previewed = True  # 같은 화면에서 바로 승인 체크를 켤 수 있게
            _preview_dialog(m["id"], targets)
        st.caption("승인한 내용만 보내요. AI가 회의록 내용을 근거로 다른 작업을 실행하지 않아요.")
        st.checkbox("미리보기 내용을 확인했고 전송을 승인합니다. 전송하면 회의 결과를 더 이상 수정할 수 없어요.",
                    key=_key(m["id"], "agree"), disabled=not previewed)
        if not previewed:
            st.caption("미리보기를 확인한 뒤 승인할 수 있어요.")
        st.button(f"{len(targets)}개 대상으로 전송", type="primary", icon=":material/send:", use_container_width=True,
                  disabled=not (targets and previewed and agreed), on_click=_send, args=(m, targets), key="send")


# ---------------------------------------------------------------- ④ 전송 결과

def _result_table(m: dict) -> None:
    _stepper(3, 3)
    owner = auth.is_owner(m)
    with panel("dv_result", flush=True):
        html('<div class="pbar"><span class="tt">전송 결과</span><span class="small muted">같은 확정 버전은 대상마다 한 번만 전송돼요. 실패한 대상만 다시 보낼 수 있어요.</span></div>')
        with st.container(key="dv_result_body"):
            cols = " ".join(f"{w}fr" for w in RESULT_GRID)
            html(f'<div class="thead" style="grid-template-columns:{cols}"><span>대상</span><span>전송 결과</span><span>상태</span><span>시각</span><span>조치</span></div>')
            for x in sorted(m["deliveries"], key=lambda x: delivery.TARGET_ORDER.index(x["target"])):
                failed = x["status"] == "FAILED"
                label, kind = DELIVERY_LABEL[x["status"]]
                with st.container(key=f"{'trf' if failed else 'tr'}_dv_{x['target']}"):
                    c = st.columns(RESULT_GRID, vertical_alignment="center")
                    c[0].markdown(f"**{target_name(x['target'])}**")
                    with c[1]:
                        html(f'<span class="{"c-err" if failed else ""}">{esc((x["error"] if failed else x["result"]) or "")}</span>')
                    with c[2]:
                        html(badge(label, kind))
                    with c[3]:
                        html(f'<span>{esc(x.get("at", ""))}</span>')
                    with c[4]:
                        if failed and owner:
                            if integrations()[x["target"]]["status"] != "connected":
                                with st.container(key=f"line_rc_{x['target']}"):
                                    st.button("다시 연결", key=f"rc_{x['target']}", on_click=_reconnect, args=(x["target"],))
                            else:
                                with st.container(key=f"line_rt_{x['target']}"):
                                    st.button("다시 보내기", key=f"rt_{x['target']}", on_click=_send, args=(m, [x["target"]]))
                        else:
                            html('<span class="muted">–</span>')
        if owner:
            rest = [t for t in delivery.TARGET_ORDER if not delivery.already_sent(m, t)]
            with st.container(key="dv_result_foot", horizontal=True, horizontal_alignment="right"):
                st.button("다른 대상에도 보내기", key="send_more", disabled=not rest, on_click=_more_mode, args=(m["id"], True))


def _more_mode(meeting_id: str, on: bool) -> None:
    """전송 결과 ↔ 대상 선택(‘다른 대상에도 보내기’). 주소에 mode=more를 붙여 이동하므로 뒤로 가기로 돌아올 수 있어요."""
    if on:
        for k in ("agree", "previewed"):
            st.session_state.pop(_key(meeting_id, k), None)
        for t in delivery.TARGET_ORDER:  # 아무것도 고르지 않은 상태로 시작 (이미 보낸 대상은 '전송 완료'로 잠김)
            st.session_state[_key(meeting_id, f"t_{t}")] = False
        router.go("delivery", meeting_id=meeting_id, mode="more")
    else:
        router.go("delivery", meeting_id=meeting_id)


# ---------------------------------------------------------------- 화면

def render() -> None:
    m = get_meeting(router.arg("meeting_id"))
    if not m or not auth.can_view(m) or not m.get("detail"):
        st.warning("회의를 찾을 수 없어요.")
        st.button("회의 목록으로", on_click=router.go, args=("meetings",))
        return
    left, actions = head_row()
    with left:
        sub = f"{m['title']}" + (f" · 확정 버전 {m['version']} ({m.get('confirmed_at') or ''} · {m.get('confirmed_by') or ''})" if m.get("version") else "")
        page_head("결과 전달", sub, ["홈", "회의", "결과 전달"], status_badge(m["status"]))
    with actions:
        st.button("결과 보기", on_click=router.go, args=("review",), kwargs={"meeting_id": m["id"]})
        if st.button("Markdown · JSON 내보내기", icon=":material/download:"):
            review_dialogs.export_dialog(m["id"])

    if m["status"] == "REVIEW_PENDING":
        notice("아직 확정하지 않은 회의예요. 결과를 확정한 뒤에 전달할 수 있어요." + (" 확정 후 내용이 바뀌어 다시 확정이 필요해요." if m.get("reconfirm") else ""), "warn")
        st.button("결과 검토로 이동", type="primary", on_click=router.go, args=("review",), kwargs={"meeting_id": m["id"]})
        return

    more = router.arg("mode") == "more" and auth.is_owner(m)
    if m["deliveries"] and not more:
        _result_table(m)
    elif auth.is_owner(m) and m["status"] in ("CONFIRMED", "SYNCED", "SYNC_PARTIAL"):
        if more:
            with st.container(horizontal=True, vertical_alignment="center"):
                notice("이미 전송한 대상은 다시 보내지 않아요. 추가로 보낼 대상만 골라 주세요.")
                st.button("‹ 전송 결과로 돌아가기", key="back_result", type="tertiary", on_click=_more_mode, args=(m["id"], False))
        _select_form(m)
    else:
        notice("전달은 회의 주관자만 할 수 있어요. 확정된 결과는 ‘결과 보기’에서 확인할 수 있어요.")
