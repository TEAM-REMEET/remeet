"""로그인 화면 아래 랜딩 섹션 (서비스 소개).

히어로 아래로 스크롤하면 이어지는 정적인 소개 영역입니다. 문구는 맨 위 상수만 고치면 됩니다.
스타일: assets/styles/landing.css
"""

from __future__ import annotations

import streamlit as st

from ui.html import markup
from ui.icons import brand_logo, icon
from utils.text import esc

# ---------------------------------------------------------------- 문구 (여기만 고치면 됩니다)
PAINS = (
    ("회의록은 쌓이는데", "어디서 무엇을 정했는지 다시 찾으려면 긴 원문을 처음부터 읽어야 해요."),
    ("할 일은 흩어지고", "누가 언제까지 하기로 했는지 메신저·메일·메모에 나뉘어 남아요."),
    ("맥락은 사라져요", "빠진 회의, 이전 결정의 이유를 새로 온 사람은 알 길이 없어요."),
)

FEATURES = (
    ("lines", "근거가 보이는 회의 정리",
     "회의록을 올리면 안건·결정·할 일로 나눠 정리해요. 요약 문장을 누르면 원문에서 근거가 된 발언이 바로 강조돼요.",
     ("안건별 논의 비중", "키워드로 원문 검색", "발언 비율")),
    ("user-check", "담당자 없는 업무에, 딱 맞는 후보 추천",
     "담당자가 정해지지 않은 일은 스킬·부서·업무 여유로 상위 3명을 추천해요. 최종 선택은 주관자가 하고, 확정 전까지 바꿀 수 있어요.",
     ("추천 이유 표시", "추천 밖 선택 이유 기록", "확정 전 변경 가능")),
    ("send", "확정하면 참석자에게 바로 전달",
     "검토를 마치고 확정하면 요약·결정·할 일을 참석자에게 보내고, 할 일은 Jira 이슈로 이어 보내요. 실패한 사람·도구에만 다시 보낼 수 있어요.",
     ("실시간 진행 상황", "실패 건만 재시도", "다른 부서에 범위 지정 공유")),
    ("flow", "RE:MAP — 회의가 쌓일수록 정리되는 프로젝트 지도",
     "프로젝트의 회의를 주제별로 묶어 안건 → 논의 → 결정·할 일까지 펼쳐 봐요. 주제마다 '의견이 갈림 · 정해지지 않음 · 정리됨'으로 나눠 무엇이 남았는지 바로 알 수 있어요.",
     ("주제 → 안건 → 결정까지 펼쳐 보기", "의견 갈림 · 미정 · 정리됨 자동 분류", "빠진 회차도 근거와 함께 따라잡기")),
    ("search", "회의 아카이브 — 지난 회의에 묻고, 근거로 답 받기",
     "쌓인 회의록 전체가 물어볼 수 있는 아카이브가 돼요. 「PG사는 어디로 정했어요?」처럼 물으면 회의·안건·원문 발언을 찾아, 문장마다 근거를 달아 답해요.",
     ("문장마다 근거 발언", "근거 있음 · 일부 근거 표시", "회의록으로 바로 이동")),
)

STEPS = (
    ("upload", "올리기", "txt · docx · md · pdf 회의록을 올리고 주제 폴더를 골라요."),
    ("lines-full", "정리", "안건·결정·할 일·키워드·일정으로 자동 정리돼요."),
    ("edit", "검토·확정", "주관자가 근거를 확인하며 고치고 담당자를 정해요."),
    ("send", "전달·연동", "참석자에게 결과를 보내고, 할 일은 Jira 이슈로 이어져요."),
)

ROLES = (
    ("주관자", "host", "회의를 정리하고 확정해요", ("요약·안건 검토와 편집", "담당자 추천에서 최종 선택", "다른 부서에 범위를 정해 공유")),
    ("참석자", "att", "내 할 일만 빠르게 확인해요", ("내 할 일과 마감일 모아 보기", "근거 발언으로 맥락 확인", "회의 아카이브 · RE:MAP으로 지난 맥락 확인")),
    ("다른 부서", "shared", "필요한 만큼만 받아 봐요", ("요약·결정만 / 원문 포함 선택", "읽기 전용으로 안전하게", "폴더 멤버가 아니어도 열람")),
)


# ---------------------------------------------------------------- 기능별 미니 화면 (목업)
def _mock_evidence() -> str:
    return (
        '<div class="mk mk-evidence">'
        '<div class="mk-col">'
        '<span class="mk-cap">회의록 원문</span>'
        '<div class="mk-line"><i>최</i><span>B안 전환율이 12% 더 높았어요.</span></div>'
        '<div class="mk-line on"><i class="host">이</i><span>그럼 3단계로 줄이고, 관심사 선택은 가입 후로 미루죠.</span></div>'
        '<div class="mk-line"><i>최</i><span>안내 문구는 제가 11/3까지 정리할게요.</span></div>'
        "</div>"
        '<div class="mk-col soft">'
        '<span class="mk-cap">핵심 요약</span>'
        '<div class="mk-sum on">온보딩을 <b>3단계로 줄이기로 확정</b>했어요.</div>'
        '<div class="mk-chips"><span class="ok">결정</span><span class="warn">길어짐</span><span class="hold">보류</span></div>'
        "</div></div>"
    )


def _mock_assign() -> str:
    rows = (("최동욱", "CS운영팀", 92, True), ("윤가은", "CS운영팀", 59, False), ("김민지", "UX디자인팀", 45, False))
    body = "".join(
        f'<div class="mk-cand{" top" if top else ""}"><b>{i + 1}. {name}</b><span class="t">{team}</span>'
        f'<span class="mk-bar"><span style="width:{score}%"></span></span><span class="sc">{score}점</span></div>'
        for i, (name, team, score, top) in enumerate(rows)
    )
    return f'<div class="mk mk-assign"><span class="mk-cap">결제 오류 고객 안내 문구 작성 · 담당자 추천</span>{body}</div>'


def _mock_ask() -> str:
    """회의 아카이브 (views/ask.py): 질문 → 근거 상태 · 답 문장 · 근거 발언 · 관련 회의."""
    return (
        '<div class="mk mk-ask">'
        '<div class="mk-q"><span>PG사는 어디로 정했어요?</span></div>'
        '<div class="mk-a">'
        '<div class="mk-a-head"><span class="mk-a-ok">근거 있음</span><span class="mk-a-src">회의 2건에서 찾았어요</span></div>'
        '<p>PG사는 <b>A사</b>로 정했어요.<sup>1</sup> 수수료가 더 낮고 정산 주기가 짧다는 점이 이유였어요.<sup>2</sup></p>'
        '<div class="mk-cite"><i>1</i><span><b>박도윤</b> 수수료가 0.2%p 낮으니 A사로 가죠.</span>'
        '<em>11/4 주간 회의</em></div>'
        '<div class="mk-cite"><i>2</i><span><b>이서윤</b> 정산도 D+1이라 운영이 편해요.</span><em>11/4 주간 회의</em></div>'
        '<div class="mk-a-links"><span>PG사 선정 회의 →</span><span>11월 릴리즈 준비 →</span></div>'
        "</div></div>"
    )


def _mock_remap() -> str:
    """RE:MAP (폴더 위키 · views/folder.py): 왼쪽 주제 트리 (하나만 펼침) · 오른쪽 주제별 상태판 세 칸."""
    tree = (
        '<div class="mk-tree">'
        '<span class="mk-t root">11월 릴리즈 준비</span>'
        '<span class="mk-t topic on">PG사 선정</span>'
        '<span class="mk-t agenda">11.4 · 수수료 · 정산 비교</span>'
        '<span class="mk-t leaf dec">결정 · A사로 계약</span>'
        '<span class="mk-t leaf todo">할 일 · 계약서 검토 <em>박도윤</em></span>'
        '<span class="mk-t topic">중복 결제 오류</span>'
        '<span class="mk-t topic">신규 온보딩</span>'
        "</div>"
    )
    cols = (("split", "의견이 갈림", "신규 온보딩", "우려 · 관심사 선택을 뒤로?"),
            ("open", "정해지지 않음", "중복 결제 오류", "미해결 · 원인 로그 확인 중"),
            ("done", "정리됨", "PG사 선정", "결정 · A사로 계약"))
    board = "".join(f'<div class="mk-col-{k}"><b>{t}</b><div class="mk-card"><span>{topic}</span><em>{note}</em></div></div>'
                    for k, t, topic, note in cols)
    return f'<div class="mk mk-remap">{tree}<div class="mk-board">{board}</div></div>'


def _mock_deliver() -> str:
    steps = (("done", "회의록 확정 저장"), ("done", "할 일·담당자 반영"), ("run", "참석자에게 결과 전송"), ("wait", "Jira 이슈로 할 일 보내기"))
    body = "".join(
        f'<div class="mk-step {st_}"><i>{"✓" if st_ == "done" else ""}</i><span>{label}</span></div>' for st_, label in steps
    )
    return (f'<div class="mk mk-deliver"><span class="mk-cap">결과 전달</span>{body}'
            '<div class="mk-progress"><span style="width:80%"></span></div>'
            '<span class="mk-note">참석자 5명 중 4명 전달됨</span></div>')


_MOCKS = (_mock_evidence, _mock_assign, _mock_deliver, _mock_remap, _mock_ask)  # FEATURES 와 같은 순서


# ---------------------------------------------------------------- 섹션
def _section_head(eyebrow: str, title: str, desc: str = "") -> str:
    return (f'<div class="ld-head"><span class="ld-eyebrow">{esc(eyebrow)}</span><h2>{title}</h2>'
            + (f"<p>{esc(desc)}</p>" if desc else "") + "</div>")


def _pains() -> None:
    cards = "".join(
        f'<div class="ld-pain"><span class="n">0{i + 1}</span><b>{esc(t)}</b><p>{esc(d)}</p></div>'
        for i, (t, d) in enumerate(PAINS)
    )
    markup(
        '<section class="ld-sec" id="overview">'
        + _section_head("WHY RE:MEET", "회의는 끝났는데,<br>정리는 아직 시작도 못 했다면")
        + f'<div class="ld-pains">{cards}</div>'
        '<p class="ld-bridge">RE:MEET은 회의록 한 개로 <b>정리 → 담당 → 전달 → 기록</b>까지 이어 줘요.</p>'
        "</section>"
    )


def _features() -> None:
    rows = []
    for i, ((ic, title, desc, points), mock) in enumerate(zip(FEATURES, _MOCKS)):
        pts = "".join(f"<li>{icon('check', 14, '#F37321', 2.6)}{esc(p)}</li>" for p in points)
        rows.append(
            f'<div class="ld-feat{" rev" if i % 2 else ""}">'
            f'<div class="tx"><span class="ic">{icon(ic, 20, "#F37321", 1.9)}</span>'
            f'<span class="k">기능 0{i + 1}</span><h3>{esc(title)}</h3><p>{esc(desc)}</p><ul>{pts}</ul></div>'
            f'<div class="vis">{mock()}</div></div>'
        )
    markup('<section class="ld-sec alt" id="features">'
           + _section_head("FEATURES", "회의 하나로 끝나는 다섯 가지 흐름")
           + "".join(rows) + "</section>")


def _steps() -> None:
    items = "".join(
        f'<li><span class="num">{i + 1}</span><span class="ic">{icon(ic, 22, "#F37321", 1.9)}</span>'
        f"<b>{esc(t)}</b><p>{esc(d)}</p></li>"
        for i, (ic, t, d) in enumerate(STEPS)
    )
    markup('<section class="ld-sec" id="how">'
           + _section_head("HOW IT WORKS", "올리고, 확인하고, 보내면 끝",
                           "회의가 끝나면 바로, 참석자 모두가 같은 결과를 받아요.")
           + f'<ol class="ld-steps">{items}</ol></section>')


def _roles() -> None:
    cards = "".join(
        f'<div class="ld-role"><span class="tag tag-{tone}">{esc(name)}</span><h3>{esc(head)}</h3>'
        f'<ul>{"".join(f"<li>{esc(p)}</li>" for p in pts)}</ul></div>'
        for name, tone, head, pts in ROLES
    )
    markup('<section class="ld-sec alt" id="roles">'
           + _section_head("FOR EVERYONE", "회의에 관련된 모두를 위해")
           + f'<div class="ld-roles">{cards}</div></section>')


def _cta() -> None:
    with st.container(key="ld-cta", gap="medium", horizontal_alignment="center"):
        markup('<div class="ld-cta-tx"><h2>회사 계정으로 시작하세요</h2>'
               "<p>계정은 관리자가 구성원 관리에서 만들어 드려요.</p></div>")
        with st.container(horizontal=True, horizontal_alignment="center", gap="small", width="content"):
            st.html('<a class="ld-up" href="#login-title">로그인하러 위로 ↑</a>', width="content")
    # <footer> 태그는 base.css 에서 Streamlit 기본 푸터와 함께 숨겨지므로 div 사용
    markup(f'<div class="ld-foot">{brand_logo(18)}<span>회의가 끝나면, 할 일과 맥락이 남아요 · 프로토타입</span>'
           "<span>© 2026 RE:MEET</span></div>")


def render_landing() -> None:
    """히어로 아래 랜딩 섹션 전체."""
    with st.container(key="landing", gap=None):
        _pains()
        _features()
        _steps()
        _roles()
        _cta()