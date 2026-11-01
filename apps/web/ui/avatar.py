"""이름 첫 글자 동그라미."""

from __future__ import annotations

from utils.text import esc


def avatar(letter: str, *, size: int = 24, host: bool = False, accent: bool = False) -> str:
    """host=True 면 주관자(주황), accent=True 면 '나' 표시용 연주황."""
    variant = "host" if host else ("accent" if accent else "plain")
    font = 11 if size < 26 else 13
    return (
        f'<span class="avatar avatar-{variant}" style="width:{size}px;height:{size}px;font-size:{font}px">'
        f"{esc(letter)}</span>"
    )
