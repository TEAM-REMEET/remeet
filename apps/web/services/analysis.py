"""회의록 분석(데모).

- 실제 AI 파이프라인 대신, 등록 시점부터 흐른 시간으로 진행 단계를 계산합니다.
- 붙여넣은 텍스트는 간단한 규칙으로 결정사항·이슈·액션 아이템을 뽑습니다.
"""
from __future__ import annotations

import re
import time
from datetime import timedelta

from data.constants import STEP_LOGS, STEP_SECONDS, STEPS
from data.templates import TEMPLATES
from utils.dates import now_time, stamp, to_date, to_iso
from utils.text import new_id, speaker_of, strip_speaker

TOTAL = len(STEPS)

# ---------------------------------------------------------------- 진행 상태

def progress_of(meeting: dict) -> dict:
    """PROCESSING 회의의 현재 단계·진행률."""
    if meeting["status"] == "FAILED":
        step = meeting["fail"]["step"]
        return {"step": step, "pct": round(step / TOTAL * 100), "done": False, "failed": True}
    if meeting["status"] != "PROCESSING":
        return {"step": TOTAL, "pct": 100, "done": True, "failed": False}
    elapsed = time.time() - (meeting.get("started_at") or time.time())
    step = min(TOTAL, int(elapsed // STEP_SECONDS))
    pct = min(100, round(elapsed / (STEP_SECONDS * TOTAL) * 100))
    return {"step": step, "pct": pct, "done": False, "failed": False, "left_sec": max(0, STEP_SECONDS * TOTAL - elapsed)}


def advance_processing(meetings: list[dict]) -> list[dict]:
    """경과 시간에 맞춰 로그를 쌓고, 끝난 회의를 검토 대기로 넘깁니다. 끝난 회의 목록을 돌려줍니다."""
    finished = []
    for m in meetings:
        if m["status"] != "PROCESSING":
            continue
        p = progress_of(m)
        logged = sum(1 for log in m["logs"] if log.get("step") is not None)
        for i in range(logged, p["step"]):
            m["logs"].insert(0, {
                "t": now_time(), "text": STEP_LOGS[i], "kind": "warn" if i == 4 else "ok", "step": i,
                "extra": "요약 검증 경고 1건과 함께 통과 · 근거 위치가 맞지 않은 주제 1개는 다시 추출해 통과했어요." if i == 4 else None,
            })
        if p["step"] >= TOTAL:
            m["status"] = "REVIEW_PENDING"
            m["analyzed_at"] = stamp()
            m["detail"] = m.get("pending_detail") or TEMPLATES[m["template"]]()
            m["pending_detail"] = None
            finished.append(m)
    return finished


def restart(meeting: dict, why: str, file_name: str | None = None) -> None:
    """실패한 회의를 전처리 단계부터 다시 시작합니다."""
    meeting["status"] = "PROCESSING"
    meeting["started_at"] = time.time() - STEP_SECONDS
    meeting["logs"] = [log for log in meeting["logs"] if log.get("step") is None]
    meeting["logs"].insert(0, {"t": now_time(), "text": "같은 파일로 다시 시도합니다." if why == "retry" else f"새 파일({file_name})로 다시 시작합니다.", "kind": "ok", "step": None})
    meeting["logs"].insert(0, {"t": now_time(), "text": STEP_LOGS[0], "kind": "ok", "step": 0})
    if why == "retry":
        meeting["retries"] -= 1
    if file_name:
        meeting["file"] = file_name

# ---------------------------------------------------------------- 텍스트 분석

WEEKDAY = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
HINTS = [
    ("디자인", ["UI 디자인"], "UX디자인팀"), ("시안", ["UI 디자인"], "UX디자인팀"), ("배지", ["UI 디자인"], "UX디자인팀"),
    ("문구", ["카피라이팅", "UX 라이팅"], "마케팅팀"), ("메일", ["이메일 마케팅"], "마케팅팀"), ("배치", ["백엔드"], "플랫폼개발팀"),
    ("로그", ["로그 설계"], "플랫폼개발팀"), ("API", ["API 설계"], "플랫폼개발팀"), ("분석", ["데이터 분석"], "데이터팀"),
    ("지표", ["지표 설계"], "데이터팀"), ("법무", ["법무 검토"], "법무팀"), ("계약", ["계약 관리"], "구매팀"),
    ("교육", ["교육 기획"], "인사팀"), ("보안", ["보안 정책"], "정보보호팀"), ("문의", ["CS 분석"], "CS운영팀"),
]


def parse_due(text: str, meeting_date: str) -> tuple[str | None, str | None]:
    """원문 기한 표현을 날짜로 바꿉니다. 날짜를 특정할 수 없으면 (None, 원문 표현)."""
    base = to_date(meeting_date)
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일\s*까지", text)
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}", m.group(0)
    if "내일까지" in text:
        return to_iso(base + timedelta(days=1)), "내일까지"
    m = re.search(r"(이번|다음)\s?주\s?([월화수목금토일])요일\s*까지", text)
    if m:
        monday = base - timedelta(days=base.weekday())
        week = 7 if m.group(1) == "다음" else 0
        return to_iso(monday + timedelta(days=week + WEEKDAY[m.group(2)])), m.group(0)
    m = re.search(r"(다음\s?주\s?중|다음\s?달\s?(?:초|중순|말)?\s?(?:까지|쯤|중)?|이번\s?달\s?(?:초|중순|말)?\s?(?:까지|중)?|\d+주\s?안에|\d{1,2}월\s?(?:초|중순|말)(?:까지|쯤)?)", text)
    if m:
        return None, m.group(0)
    return None, None


def detail_from_text(text: str, meeting: dict, members: list[dict]) -> dict:
    lines = [s.strip() for s in text.splitlines() if s.strip()][:120]
    segments = [{"id": i + 1, "text": t} for i, t in enumerate(lines)]
    n_topics = min(4, max(1, round(len(lines) / 5)))
    size = max(1, -(-len(lines) // n_topics))
    topics = []
    for i in range(0, len(lines), size):
        to = min(len(lines), i + size)
        first = strip_speaker(lines[i])
        topics.append({"id": f"t{len(topics) + 1}", "title": first[:24] + ("…" if len(first) > 24 else ""), "from": i + 1, "to": to,
                       "note": " ".join(strip_speaker(x) for x in lines[i:to][:2]), "edited": False})

    def topic_of(n: int) -> str:
        return next((t["id"] for t in topics if t["from"] <= n <= t["to"]), topics[0]["id"])

    def member_named(name: str | None) -> str | None:
        if not name:
            return None
        hit = next((x for x in members if x["name"] == name or x["name"][1:] == name), None)
        return hit["id"] if hit else None

    decisions, issues, actions = [], [], []
    for s in segments:
        body = strip_speaker(s["text"])
        if re.search(r"확정하겠|하기로|확정합니다|결정했", body):
            decisions.append({"id": new_id("d"), "text": body, "topic": topic_of(s["id"]), "refs": [s["id"]], "edited": False})
        elif re.search(r"다음에\s?(다시\s?)?(정하|얘기|논의)|결론을 못|확인이 필요", body):
            issues.append({"id": new_id("i"), "title": body[:34] + ("…" if len(body) > 34 else ""), "text": "결론을 내지 못하고 넘어간 논의입니다.",
                           "topic": topic_of(s["id"]), "refs": [s["id"]], "to_next": True, "moved": False})
        is_task = re.search(r"(겠습니다|게요|해 ?주세요|부탁)", body) or re.search(r"필요하겠|필요합니다", body)
        if is_task and not re.search(r"확정하겠|하기로|^오늘은|수고하셨", body):
            due, raw = parse_due(body, meeting["date"])
            ask = re.search(r"([가-힣]{2,4})\s?님", body)
            assignee = member_named(ask.group(1)) if ask else None
            if not assignee and re.search(r"(제가|겠습니다|게요)", body):
                assignee = member_named(speaker_of(s["text"]))
            hint = next((h for h in HINTS if h[0] in body), None)
            actions.append({
                "id": new_id("a"), "title": re.sub(r"[.。]$", "", re.sub(r"\s?제가\s", " ", re.sub(r"^(네,?\s*)", "", body))),
                "topic": topic_of(s["id"]), "assignee": assignee, "source": "explicit" if assignee else None,
                "due": due, "due_raw": raw, "priority": "보통", "ai_priority": "보통", "refs": [s["id"]], "review": "draft",
                "estimated": not assignee and bool(re.search(r"필요하겠|필요합니다", body)),
                "need_skills": hint[1] if hint else [], "need_dept": hint[2] if hint else "",
                "keep": False, "edited": False, "manual": False, "work": "todo", "remind": True, "ext": None,
            })
    return {
        "purpose": meeting.get("purpose") or "입력된 회의 목적이 없습니다.",
        "core": f"주제 {len(topics)}개에서 결정사항 {len(decisions)}건, 액션 아이템 {len(actions)}건, 미해결 이슈 {len(issues)}건을 찾았습니다.",
        "core_edited": False, "segments": segments, "topics": topics, "decisions": decisions, "issues": issues,
        "agenda": [{"id": new_id("n"), "text": i["title"], "issue": i["id"]} for i in issues],
        "actions": actions, "history": [{"t": now_time(), "who": "시스템", "text": "AI 분석 완료, 초안 생성", "changes": []}],
    }


def new_meeting(form: dict, organizer: dict, source_text: str, members: list[dict], demo_note: str | None) -> dict:
    m = {
        "id": new_id("mt"), "title": form["title"], "dept": organizer["dept"], "date": form["date"], "start": form["start"], "end": form["end"],
        "organizer": organizer["id"], "attendees": list(dict.fromkeys([organizer["id"], *form["attendees"]])), "file": form["file_name"],
        "ref_file": form.get("ref_file"), "purpose": form.get("purpose", ""), "status": "PROCESSING", "started_at": time.time(),
        "logs": [], "access": "attendees", "version": 0, "deliveries": [], "retries": 2, "detail": None, "template": None,
        "demo_note": demo_note, "reconfirm": False,
    }
    m["pending_detail"] = detail_from_text(source_text, m, members)
    return m
