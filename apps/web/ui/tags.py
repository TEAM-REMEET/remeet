"""상태·역할 태그 (배지)."""

from __future__ import annotations

from utils.text import esc

# tone → CSS 클래스 (components.css 의 .tag-*)
TONES = {
    "host", "att", "abs", "shared", "ok", "hold", "warn", "split", "info",
    "mine", "deep", "fail", "wait", "run", "on", "off", "expired",
}

_MD_COLORS = {"host": "orange", "mine": "orange", "att": "gray", "abs": "blue", "shared": "violet", "info": "violet"}


def tag(label: str, tone: str = "att", *, extra_class: str = "", pulse: bool = False) -> str:
    tone = tone if tone in TONES else "att"
    cls = f"tag tag-{tone} {extra_class}".strip() + (" pulse" if pulse else "")
    return f'<span class="{cls}">{esc(label)}</span>'


# 남은 단계가 있는 회의의 배지 (상태 → 아이콘 · 글자 · 색). 1~5단계를 다 마친 회의(done)는 배지 없음
#   review    확정하기를 마치지 않음 (담당자 추천 · 최종 회의록 남음)
#   confirmed 최종 회의록은 만들었지만 참석자에게 결과 전달 전
#   sending   결과 전달 중 · fail 전송 실패 (다시 보내야 함)
#   unexported 결과 전달은 마쳤지만 외부 도구 전송 전 (selectors.stage_of)
_STAGE_BADGES = {
    "review": ("edit", "검토 중", "warn", "#6E5200"),
    "confirmed": ("send", "전달 전", "warn", "#6E5200"),
    "sending": ("send", "전달 중", "run", "#1A1A1A"),
    "fail": ("send", "재전송 필요", "fail", "#B3261E"),
    "deliver_fail": ("send", "결과 재전송 필요", "fail", "#B3261E"),
    "unexported": ("upload", "외부 도구 전송 전", "warn", "#6E5200"),
}


# 사이드바처럼 좁은 곳: (짧은 글자, 색, 마우스를 올리면 보이는 설명)
_STAGE_SHORT = {
    "review": ("검토 중", "warn", "검토 진행 중 · 확정하기 전"),
    "confirmed": ("전달 전", "warn", "최종 회의록 · 결과 전달 전"),
    "sending": ("전달 중", "run", "참석자에게 결과 전달 중"),
    "fail": ("재전송", "fail", "보내지 못한 곳이 있어 다시 보내야 해요"),
    "unexported": ("전송 전", "warn", "결과 전달 완료 · 외부 도구 전송 전"),
}


def stage_trail(status: str) -> str:
    """사이드바 회의 줄 오른쪽 작은 배지 (남은 단계가 없으면 빈 글자)."""
    if status not in _STAGE_SHORT:
        return ""
    label, tone, title = _STAGE_SHORT[status]
    return f'<span class="nav-trail stage tone-{tone}" title="{esc(title)}">{esc(label)}</span>'


def stage_badge(status: str) -> str:
    """남은 단계가 있으면 아이콘이 붙은 배지, 없으면 빈 글자."""
    if status not in _STAGE_BADGES:
        return ""
    from ui.icons import icon  # ui.icons 가 config 를 불러서 함수 안에서

    name, label, tone, color = _STAGE_BADGES[status]
    return f'<span class="tag tag-{tone} tag-stage">{icon(name, 13, color, 2.4)}{esc(label)}</span>'


def badge_count(value: object, active: bool = False) -> str:
    return f'<span class="cnt{" on" if active else ""}">{esc(value)}</span>'


def soft_label(label: str) -> str:
    """'안건 1' 같은 회색 작은 라벨."""
    return f'<span class="soft-label">{esc(label)}</span>'

def md_tag(label: str, tone: str = "att") -> str:
    return f":{_MD_COLORS.get(tone, 'gray')}-background[**{label}**]"