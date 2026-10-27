"""모든 화면을 계정별로 열어 오류가 없는지 확인합니다.  실행: python tests/smoke.py"""
import sys
from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "app.py")
fails = 0

def run(label, user, page, **args):
    global fails
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    if user:
        from core import auth  # noqa
        at.session_state["user"] = next(u for u in __import__("data.seed", fromlist=["USERS"]).USERS if u["id"] == user)
        at.session_state["_route_pending"] = (page, {k: str(v) for k, v in args.items()})
    at.run()
    if at.exception:
        fails += 1
        print("FAIL", label, user, page, args)
        for e in at.exception:
            print("   ", e.message, "\n", "\n".join(e.stack_trace[-6:]))
    else:
        print("ok  ", label, user, page, args)
    return at

sys.path.insert(0, APP.rsplit("/", 1)[0])
run("main-out", None, "main")
mids = ["mt-mkt", "mt-onb", "mt-cs", "mt-vendor", "mt-grade", "mt-sprint", "mt-app", "mt-hr", "mt-price", "mt-gov", "mt-sec"]
for u in ["seoyeon.lee", "junho.park", "admin"]:
    for p in ["main", "dashboard", "meetings", "register", "members", "integrations"]:
        run(p, u, p)
    run("search", u, "search", q="요금제")
    run("search-empty", u, "search")
    for mid in mids:
        run("progress", u, "progress", meeting_id=mid)
        run("review", u, "review", meeting_id=mid)
        run("delivery", u, "delivery", meeting_id=mid)
run("meetings-filter", "seoyeon.lee", "meetings", filter="FAIL", scope="org")
run("review-focus", "seoyeon.lee", "review", meeting_id="mt-sprint", focus="a1")
print("FAILS", fails)
