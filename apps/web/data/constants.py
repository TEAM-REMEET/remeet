"""화면 전반에서 쓰는 코드값과 라벨."""

# 회의 상태 (PRD 3.7 상태 흐름)
STATUS = {
    "PROCESSING": ("분석 중", "run"),
    "REVIEW_PENDING": ("검토 대기", "wait"),
    "CONFIRMED": ("확정 · 전달 전", "ok"),
    "SYNCED": ("전달 완료", "sent"),
    "SYNC_PARTIAL": ("일부 전달 실패", "err"),
    "FAILED": ("분석 실패", "err"),
}

# 분석 파이프라인 단계
STEPS = ["업로드", "전처리", "주제 분석", "요약 · 액션 추출", "검증", "담당자 추천", "리뷰 준비"]
STEP_LOGS = [
    "회의록을 받고 형식·용량·빈 문서 여부를 확인했어요.",
    "본문을 문단 단위로 정리하고 원문 위치를 기록했어요.",
    "주제가 바뀌는 지점을 찾고, 앞뒤 문맥을 확인해 주제별로 나눴어요.",
    "주제별로 요약과 액션 아이템을 동시에 추출했어요.",
    "요약과 액션 아이템을 각각 원문과 대조해 검증했어요.",
    "담당자가 정해지지 않은 업무에 후보를 추천했어요.",
    "검토 화면을 준비했어요.",
]
STEP_SECONDS = 3.0  # 데모: 한 단계에 걸리는 시간

# 액션 아이템 검토 상태 / 업무 진행 상태
REVIEW_LABEL = {"draft": "검토 전", "approved": "승인", "rejected": "거절"}
WORK_LABEL = {"todo": "시작 전", "doing": "진행 중", "done": "완료"}

# 담당자 출처 (PRD 3.5)
SOURCE_LABEL = {"explicit": "원문 명시", "suggested": "AI 추천 채택", "manual": "직접 지정"}

ACCESS_LABEL = {"attendees": "참석자", "team": "팀 전체", "organizer": "주관자만"}

PRIORITIES = ["높음", "보통", "낮음"]

ALLOWED_EXT = ["txt", "md", "docx", "hwpx"]
MAX_FILE_MB = 20

# 전달 상태
DELIVERY_LABEL = {"SENT": ("전송 완료", "ok"), "PENDING": ("전송 중", "run"), "FAILED": ("실패", "err")}
INTEG_STATUS = {"connected": ("연결됨", "ok"), "expired": ("인증 만료", "err"), "disconnected": ("미연결", "neutral")}
