"""스타일이 입혀진 버튼.

Streamlit 버튼은 직접 class 를 줄 수 없어서, `st.container(key="b-<variant>-<key>")` 로 감싸고
assets/styles/components.css 에서 `[class*="st-key-b-<variant>-"]` 선택자로 꾸밉니다.

variant 이름에는 하이픈을 쓰지 마세요 (선택자가 겹치지 않도록). 예: chip / chipon
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import streamlit as st

from utils.text import safe_key

VARIANTS = {
    "primary",  # 주황 채움 (주요 행동)
    "secondary",  # 흰 바탕 + 회색 테두리
    "ghost",  # 투명
    "dark",  # 진회색 채움 (닫기 등)
    "danger",  # 빨강 채움
    "dangerline",  # 흰 바탕 + 빨강 글자
    "accentline",  # 흰 바탕 + 주황 테두리 (검토하기 / 전달하기)
    "link",  # 표 제목처럼 글자만
    "back",  # '← 폴더' 작은 회색 링크
    "mini",  # '복사' 같은 작은 버튼
    "chip",
    "chipon",  # 필터 칩
    "seg",
    "segon",  # 회색 트랙 안 세그먼트
    "tab",
    "tabon",  # 밑줄 탭
    "pick",
    "pickon",  # 근거 강조용 문장 버튼
    "src",  # 위키 출처 칩
    "rel",  # 위키 관계 링크 칩
    "card",  # 카드 전체가 버튼
    "dashed",  # 점선 테두리
    "nav",
    "navon",  # 사이드바 메뉴
    "navsub",
    "navsubon",  # 사이드바 하위 폴더
    "kw",
    "kwon",  # 키워드 칩
    "noti",  # 알림: 안 읽음
    "notiread",  # 알림: 읽음
}


def button(
    label: str,
    key: str,
    *,
    variant: str = "secondary",
    on_click: Callable[..., Any] | None = None,
    args: Sequence[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    icon: str | None = None,
    width: str = "content",
    disabled: bool = False,
    help: str | None = None,
) -> bool:
    assert variant in VARIANTS, f"unknown button variant: {variant}"
    key = safe_key(key)
    with st.container(key=f"b-{variant}-{key}", width="stretch" if width == "stretch" else "content"):
        return st.button(
            label,
            key=key,
            on_click=on_click,
            args=args,
            kwargs=kwargs,
            icon=icon,
            width=width,
            disabled=disabled,
            help=help,
            type="primary" if variant == "primary" else "secondary",
        )


def button_group(
    options: Sequence[tuple[str, str]],
    selected: str,
    on_select: Callable[[str], Any],
    *,
    key: str,
    style: str = "chip",
    gap: str = "xsmall",
) -> None:
    """여러 개 중 하나를 고르는 버튼 줄. style: chip | seg | tab | kw

    options: [(value, label), ...]  — label 에 '전체 5' 처럼 개수를 붙여 넘기면 됩니다.
    """
    container_key = f"grp-{style}-{safe_key(key)}"
    with st.container(horizontal=True, gap=gap, key=container_key, width="stretch" if style == "tab" else "content"):
        for value, label in options:
            on = value == selected
            button(
                label,
                key=f"{key}_{value}",
                variant=f"{style}on" if on else style,
                on_click=on_select,
                args=(value,),
            )
