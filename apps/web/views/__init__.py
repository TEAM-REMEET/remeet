"""페이지 (화면 단위).

AppState.screen 값 → 렌더 함수. app.py 가 이 표를 보고 화면을 고릅니다.
"""

from views import (
    delivery,
    folder,
    home,
    integrations,
    my_wiki,
    shared_received,
    shared_sent,
)
from views.meeting import render as render_meeting

ROUTES = {
    "home": home.render,
    "folder": folder.render,
    "note": render_meeting,
    "deliver": delivery.render,
    "sent": shared_sent.render,
    "recv": shared_received.render,
    "mcp": integrations.render,
    "wiki": my_wiki.render,
}
