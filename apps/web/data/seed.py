"""데모 시드 데이터: 구성원, 계정, 외부 연동, 회의 목록, 알림."""
from __future__ import annotations

import time
from pathlib import Path

from data.constants import STEP_SECONDS
from data.templates import TEMPLATES

MEMBERS = [
    {"id": "m1", "name": "이서연", "dept": "서비스기획팀", "role": "PM", "skills": ["기획", "요구사항 정의", "데이터 분석", "UX 라이팅"], "load": 5, "projects": ["온보딩 개선", "요금제 개편", "회원 등급 개편"], "history": ["신규 요금제 구조 정리", "회원 등급 개편안 작성"]},
    {"id": "m2", "name": "박준호", "dept": "플랫폼개발팀", "role": "백엔드 개발자", "skills": ["백엔드", "로그 설계", "API 설계", "데이터 파이프라인"], "load": 8, "projects": ["로그인 개선", "이벤트 수집"], "history": ["로그인 API 개선", "면접관 풀 정리", "푸시 큐 모니터링 대시보드"]},
    {"id": "m3", "name": "김민지", "dept": "UX디자인팀", "role": "프로덕트 디자이너", "skills": ["UI 디자인", "UX 라이팅", "프로토타이핑"], "load": 3, "projects": ["모바일 앱 2.4", "온보딩 개선"], "history": ["설정 화면 개편 시안"]},
    {"id": "m4", "name": "최동욱", "dept": "CS운영팀", "role": "매니저", "skills": ["CS 분석", "안내 문구", "고객 응대 정책", "UX 라이팅"], "load": 4, "projects": ["CS 정책 개정"], "history": ["결제 실패 안내 문구 개선", "환불 안내 문구 개선", "CS 문의 유형 분류 체계 정비"]},
    {"id": "m5", "name": "정하은", "dept": "마케팅팀", "role": "CRM 마케터", "skills": ["CRM", "A/B 테스트", "이메일 마케팅", "카피라이팅"], "load": 5, "projects": ["웰컴 캠페인", "회원 등급 개편"], "history": ["3분기 CRM 캠페인 리포트"]},
    {"id": "m6", "name": "오세훈", "dept": "데이터팀", "role": "데이터 분석가", "skills": ["데이터 분석", "SQL", "지표 설계", "A/B 테스트"], "load": 6, "projects": ["데이터 거버넌스 TF", "온보딩 개선"], "history": ["가입 퍼널 대시보드 구축"]},
    {"id": "m7", "name": "윤가은", "dept": "CS운영팀", "role": "사원", "skills": ["CS 분석", "응대 매뉴얼"], "load": 7, "projects": ["CS 정책 개정"], "history": ["응대 매크로 정비"]},
    {"id": "m8", "name": "한지수", "dept": "구매팀", "role": "과장", "skills": ["계약 관리", "협력사 관리"], "load": 4, "projects": ["협력사 계약 갱신"], "history": []},
    {"id": "m9", "name": "김태윤", "dept": "정보보호팀", "role": "보안 담당", "skills": ["보안 정책", "교육 기획", "법무 협의"], "load": 3, "projects": ["사내 보안 교육"], "history": ["보안 사고 대응 매뉴얼 개정"]},
    {"id": "m10", "name": "윤서진", "dept": "인사팀", "role": "HRBP", "skills": ["채용", "교육 기획"], "load": 4, "projects": ["하반기 채용"], "history": []},
    {"id": "m11", "name": "강도현", "dept": "법무팀", "role": "변호사", "skills": ["법무 검토", "개인정보 보호", "약관"], "load": 5, "projects": ["요금제 개편"], "history": ["요금제 약관 개정 검토"]},
    {"id": "m12", "name": "김관리", "dept": "경영지원팀", "role": "시스템 관리자", "skills": ["시스템 운영"], "load": 2, "projects": [], "history": [], "no_recommend": True},
]

USERS = [
    {"id": "seoyeon.lee", "member_id": "m1", "role": "organizer", "role_label": "회의 주관자", "desc": "회의를 등록하고 결과를 확정·전달합니다"},
    {"id": "junho.park", "member_id": "m2", "role": "member", "role_label": "참석자", "desc": "참석한 회의 결과를 열람하고 내 업무를 확인합니다"},
    {"id": "admin", "member_id": "m12", "role": "admin", "role_label": "시스템 관리자", "desc": "구성원 프로필과 연동 설정을 관리합니다"},
]
DEMO_PASSWORD = "remeet"

INTEGRATIONS = {
    "slack": {"name": "Slack", "desc": "채널에 회의 결과를 공유합니다", "status": "connected", "account": "사내 워크스페이스", "phase": 1, "markdown": False},
    "email": {"name": "이메일", "desc": "참석자에게 회의 결과를 메일로 보냅니다", "status": "connected", "account": "사내 메일 서버", "phase": 1, "markdown": True},
    "calendar": {"name": "캘린더", "desc": "다음 회의 일정을 등록합니다", "status": "connected", "account": "팀 공용 캘린더", "phase": 1, "markdown": False},
    "jira": {"name": "Jira", "desc": "액션 아이템을 이슈로 만듭니다", "status": "expired", "account": "ONB · SEC 프로젝트", "phase": 1, "markdown": False},
    "notion": {"name": "Notion", "desc": "회의록 페이지를 만듭니다", "status": "connected", "account": "팀 위키", "phase": 1, "markdown": True},
    "teams": {"name": "Microsoft Teams", "desc": "팀 채널에 회의 결과를 공유합니다", "status": "disconnected", "account": "", "phase": 2, "markdown": False},
    "confluence": {"name": "Confluence", "desc": "회의록 페이지를 만듭니다", "status": "disconnected", "account": "", "phase": 2, "markdown": True},
}

NOTIFICATIONS = [
    {"id": "nt1", "kind": "overdue", "text": "‘보안 교육 공지 문안 작성’ 기한이 1일 지났어요.", "meeting_id": "mt-sec", "time": "09:00", "read": False},
    {"id": "nt2", "kind": "soon", "text": "‘요금제 비교표 문구 법무 검토 요청’ 마감이 내일이에요.", "meeting_id": "mt-price", "time": "09:00", "read": False},
    {"id": "nt3", "kind": "fail", "text": "Jira 인증이 만료되어 ‘사내 보안 교육 계획 수립’ 전달 1건이 실패했어요.", "meeting_id": "mt-sec", "time": "어제", "read": False},
    {"id": "nt4", "kind": "done", "text": "‘4분기 신규 회원 온보딩 개선 회의’ 분석이 끝났어요. 검토해 주세요.", "meeting_id": "mt-onb", "time": "14:36", "read": True},
]

SAMPLE_TEXT = (Path(__file__).parent / "sample_transcript.txt").read_text(encoding="utf-8")


def _sent(target: str, result: str, at: str) -> dict:
    return {"target": target, "status": "SENT", "result": result, "at": at, "error": None}


def _meeting(**kw) -> dict:
    base = {"access": "attendees", "version": 0, "deliveries": [], "retries": 2, "purpose": "", "ref_file": None,
            "detail": None, "pending_detail": None, "started_at": None, "logs": [], "reconfirm": False}
    base.update(kw)
    return base


def seed_meetings() -> list[dict]:
    now = time.time()
    rows = [
        _meeting(id="mt-mkt", title="3분기 마케팅 성과 리뷰", dept="마케팅팀", date="2026-10-22", start="10:00", end="11:00", organizer="m5", attendees=["m5", "m1", "m6", "m3"], file="3분기_성과리뷰.docx", status="PROCESSING", template="mkt", started_at=now - STEP_SECONDS * 3),
        _meeting(id="mt-onb", title="4분기 신규 회원 온보딩 개선 회의", dept="서비스기획팀", date="2026-10-21", start="14:00", end="15:10", organizer="m1", attendees=["m1", "m2", "m3", "m4", "m5", "m6"], file="온보딩개선_1021.hwpx", ref_file="온보딩_퍼널_9월.pdf", status="REVIEW_PENDING", analyzed_at="10.22 14:36", template="onb"),
        _meeting(id="mt-cs", title="고객센터 응대 정책 개정 논의", dept="CS운영팀", date="2026-10-20", start="16:00", end="17:00", organizer="m4", attendees=["m4", "m7", "m1", "m11"], file="CS정책개정.txt", status="REVIEW_PENDING", analyzed_at="10.20 17:12", template="cs"),
        _meeting(id="mt-grade", title="회원 등급 개편 1차 논의", dept="서비스기획팀", date="2026-10-19", start="15:00", end="16:00", organizer="m1", attendees=["m1", "m5", "m6", "m2"], file="등급개편_1차.md", status="CONFIRMED", version=1, confirmed_at="10.20 16:40", confirmed_by="이서연", analyzed_at="10.19 16:12", template="grade"),
        _meeting(id="mt-sprint", title="주간 개발 스프린트 회의 (42주차)", dept="플랫폼개발팀", date="2026-10-19", start="09:30", end="10:20", organizer="m2", attendees=["m2", "m6", "m1"], file="sprint_w42.md", status="SYNCED", version=1, confirmed_at="10.19 11:05", confirmed_by="박준호", template="sprint",
                 deliveries=[_sent("slack", "#플랫폼-개발 채널에 게시", "10.19 11:07"), _sent("jira", "이슈 1건 생성 · PLT-412", "10.19 11:07")]),
        _meeting(id="mt-vendor", title="협력사 계약 갱신 사전 검토", dept="구매팀", date="2026-10-16", start="15:00", end="16:00", organizer="m8", attendees=["m8", "m9", "m1"], file="계약갱신_사전검토.docx", status="REVIEW_PENDING", analyzed_at="10.16 16:20", template="vendor"),
        _meeting(id="mt-gov", title="데이터 거버넌스 TF 2차 회의", dept="데이터팀", date="2026-10-15", start="13:30", end="15:00", organizer="m1", attendees=["m1", "m6", "m9", "m11", "m2"], file="거버넌스TF_2차.hwpx", status="FAILED", template="gov", failed_at="10.22 15:01",
                 fail={"title": "회의록 파일을 읽지 못했어요", "text": "파일이 손상되었거나 암호가 설정되어 있어 본문을 꺼낼 수 없습니다. 자동으로 한 번 더 시도했지만 같은 문제가 발생했습니다.", "code": "PARSE_FAILED", "step": 1},
                 logs=[{"t": "15:01", "text": "재시도 실패 · 분석을 멈췄어요.", "kind": "err"}, {"t": "15:01", "text": "본문 추출 실패, 자동 재시도 (1/1)", "kind": "err"}, {"t": "15:01", "text": "회의록을 받고 형식·용량·빈 문서 여부를 확인했어요.", "kind": "ok"}]),
        _meeting(id="mt-sec", title="사내 보안 교육 계획 수립", dept="정보보호팀", date="2026-10-14", start="11:00", end="11:40", organizer="m1", attendees=["m9", "m10", "m1", "m12"], file="보안교육계획.docx", status="SYNC_PARTIAL", version=1, confirmed_at="10.14 14:00", confirmed_by="이서연", template="sec",
                 deliveries=[_sent("slack", "#보안-공지 채널에 게시", "10.14 14:02"), _sent("email", "참석자 4명에게 발송", "10.14 14:02"),
                             {"target": "jira", "status": "FAILED", "at": "10.14 14:02", "result": None, "error": "Jira 인증이 만료되었어요(401). 다시 연결한 뒤 재시도해 주세요."}]),
        _meeting(id="mt-app", title="모바일 앱 2.4 릴리스 회고", dept="앱개발팀", date="2026-10-13", start="17:00", end="18:00", organizer="m3", attendees=["m3", "m2", "m1"], file="release24_회고.md", status="SYNCED", version=1, confirmed_at="10.14 10:10", confirmed_by="김민지", template="app",
                 deliveries=[_sent("slack", "#앱-개발 채널에 게시", "10.14 10:12"), _sent("email", "참석자 3명에게 발송", "10.14 10:12"), _sent("jira", "이슈 2건 생성 · APP-88, APP-89", "10.14 10:12")]),
        _meeting(id="mt-hr", title="하반기 채용 계획 공유", dept="인사팀", date="2026-10-12", start="10:00", end="10:50", organizer="m10", attendees=["m10", "m2", "m3"], file="하반기채용.txt", status="SYNCED", version=1, confirmed_at="10.12 14:30", confirmed_by="윤서진", template="hr",
                 deliveries=[_sent("email", "참석자 3명에게 발송", "10.12 14:31"), _sent("calendar", "다음 회의 일정 등록", "10.12 14:31")]),
        _meeting(id="mt-price", title="신규 요금제 가격 정책 논의", dept="사업전략팀", date="2026-10-08", start="14:00", end="15:30", organizer="m1", attendees=["m1", "m5", "m11"], file="요금제정책.docx", status="SYNCED", version=1, confirmed_at="10.08 17:00", confirmed_by="이서연", template="price",
                 deliveries=[_sent("slack", "#사업전략 채널에 게시", "10.08 17:02"), _sent("notion", "회의록 페이지 생성", "10.08 17:02")]),
    ]
    for m in rows:
        detail = TEMPLATES[m["template"]]()
        if m["status"] in ("PROCESSING", "FAILED"):
            m["pending_detail"] = detail
        else:
            m["detail"] = detail
    return rows
