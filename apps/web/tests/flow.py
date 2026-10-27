"""클릭 흐름 테스트: 로그인 → 검토(근거·액션 펼치기·모달) → 확정 → 전달 → 등록 → 재시도 → 구성원 → 로그아웃.

실행: python tests/flow.py   (apps/web 폴더에서)
"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "app.py")
at = AppTest.from_file(APP, default_timeout=30)
at.run()


def chk(step):
    if at.exception:
        print("FAIL", step)
        for e in at.exception:
            print(e.message, "\n".join(e.stack_trace[-8:]))
        sys.exit(1)
    print("ok", step)


def has(label):
    return any(b.label == label for b in at.button)


def btn(label, nth=0):
    bs = [b for b in at.button if b.label == label]
    assert bs, f"no button {label}: {[b.label for b in at.button][:80]}"
    bs[nth].click()
    at.run()


def goto(page, **args):
    """URL 이동과 같은 효과: router가 다음 실행 때 st.switch_page로 이동합니다."""
    at.session_state["_route_pending"] = (page, {k: str(v) for k, v in args.items()})
    at.run()


def route():
    return at.session_state["_route"]


def open_action4():
    goto("review", meeting_id="mt-onb")
    btn("액션 아이템")
    if has("액션 4 펼치기"):
        btn("액션 4 펼치기")


# 로그인 · 대시보드
at.text_input(key="login_id").input("seoyeon.lee")
at.text_input(key="login_pw").input("remeet")
btn("로그인"); chk("login")
assert route()["page"] == "dashboard"
for label in ["검토 대기 회의 보기", "회의 목록"]:
    goto("dashboard"); btn(label); chk("dashboard → " + label)
assert route()["page"] == "meetings"

# 결과 검토: 근거 강조 · 액션 펼치기 · 담당자 · 수정 · 승인
goto("review", meeting_id="mt-onb"); chk("review")
assert route() == {"page": "review", "args": {"meeting_id": "mt-onb"}}
btn("안건 1 · 관심사 선택 단계 이탈 근거 보기"); chk("topic highlight")
assert at.session_state["rv_sel_mt-onb"]["refs"][0] == 1
goto("review", meeting_id="mt-onb"); btn("액션 아이템"); chk("actions tab")
btn("액션 4 펼치기"); chk("expand action")
btn("담당자 지정"); chk("reco dialog")
btn("지정"); chk("assign top recommendation")
open_action4(); btn("수정"); chk("edit dialog")
open_action4(); btn("승인"); chk("approve inline")

# 결과 검토: 더보기 메뉴 · 탭별 버튼 · 모달
for tab, label in [(None, "회의 정보 · 공개 범위"), (None, "변경 이력"), (None, "내보내기 (Markdown · JSON)"), (None, "전체 미리보기"),
                   (None, "요약 편집"), ("다음 회의 안건", "안건 편집"), ("액션 아이템", "추가")]:
    goto("review", meeting_id="mt-onb"); btn("요약")
    if tab:
        btn(tab)
    btn(label); chk(label)
goto("review", meeting_id="mt-onb"); btn("액션 아이템"); btn("남은 항목 모두 승인"); chk("approve all")
btn("확정하기"); chk("confirm dialog")

# 확정 → 전달 (모달 안 버튼은 AppTest가 흉내 내지 못해 직접 확정 상태로 바꿉니다)
m = [x for x in at.session_state["meetings"] if x["id"] == "mt-onb"][0]
m.update(status="CONFIRMED", version=1, confirmed_at="10.22 15:00")
goto("delivery", meeting_id="mt-onb"); chk("delivery")
btn("미리보기 확인하기"); chk("delivery preview")
at.checkbox(key="dv_agree_mt-onb").check(); at.run()
btn("3개 대상으로 전송"); chk("send")
goto("delivery", meeting_id="mt-onb"); chk("delivery result table")
print("  →", m["status"], [(d["target"], d["status"]) for d in m["deliveries"]])
goto("integrations"); btn("다시 연결"); chk("integration reconnect")
goto("review", meeting_id="mt-onb", focus="a1"); chk("tracking + focus")

# 다른 대상에도 보내기 → 주소에 mode=more, 돌아가면 다시 전송 결과
goto("delivery", meeting_id="mt-sec"); chk("delivery result (mt-sec)")
btn("다른 대상에도 보내기"); chk("send to other targets → selection page")
assert route()["args"].get("mode") == "more" and has("미리보기 확인하기")
btn("‹ 전송 결과로 돌아가기"); chk("back to result")
assert "mode" not in route()["args"] and has("다른 대상에도 보내기")
goto("meetings"); goto("delivery", meeting_id="mt-sec"); chk("re-enter delivery → result first")
assert has("다른 대상에도 보내기") and not has("미리보기 확인하기")

# 새 회의 등록 · 분석 실패 재시도
goto("register"); btn("샘플 회의록으로 채우기"); chk("sample")
btn("분석 시작"); chk("start analysis")
assert route()["page"] == "progress", [e.value for e in at.error]
goto("progress", meeting_id="mt-gov"); btn("같은 파일로 다시 시도 (남은 2회)"); chk("retry failed analysis")

# 구성원 · 검색 · 로그아웃
goto("members"); btn("박준호 정보 보기"); chk("member dialog")
goto("search", q="요금제"); chk("search")
btn("로그아웃"); chk("logout")
assert route()["page"] == "main"