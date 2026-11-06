"""액션 아이템을 직접 추가했을 때 원문을 다시 훑어 근거 발언을 찾는 단계.

실제 서비스에서는 여기서 LLM 을 호출합니다 (추가한 할 일 + 회의 원문 → 근거 발언 id 목록).
지금은 목업이라 할 일의 낱말이 많이 겹치는 발언을 고릅니다 (로딩 화면 없이 바로 추가).
`rescan` 의 시그니처만 유지하고 내부를 실제 호출로 바꾸면 화면은 그대로 동작해요.
"""

from __future__ import annotations

import re

from data.models import Meeting

_MAX_SRC = 2


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[0-9A-Za-z가-힣]+", text) if len(w) >= 2]


def rescan(m: Meeting, text: str) -> tuple[str, ...]:
    """원문(m.paragraphs)에서 할 일(text)의 근거가 될 발언 id 를 찾습니다. 못 찾으면 빈 튜플."""
    words = _words(text)
    scored = []
    for p in m.paragraphs:
        if p.is_heading or not p.speaker:
            continue
        # 조사가 붙어도 맞도록 두 글자 이상 낱말은 끝 한 글자를 떼고도 비교
        score = sum(1 for w in words if w in p.text or (len(w) > 2 and w[:-1] in p.text))
        if score:
            scored.append((score, p.id))
    scored.sort(key=lambda x: -x[0])
    return tuple(pid for _, pid in scored[:_MAX_SRC])
