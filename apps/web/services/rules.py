"""검토 경고와 담당자 추천 규칙 (PRD 3.4 · 3.5)."""
from __future__ import annotations

SCORE_WEIGHTS = {"skill": 0.4, "role": 0.4, "ease": 0.2}
RECOMMEND_THRESHOLD = 55
OVERLOAD = 8


def member_by_id(members: list[dict], member_id: str | None) -> dict | None:
    return next((m for m in members if m["id"] == member_id), None)


def member_name(members: list[dict], member_id: str | None, empty: str = "미지정") -> str:
    m = member_by_id(members, member_id)
    return m["name"] if m else empty


def warnings_of(action: dict, members: list[dict]) -> list[dict]:
    """액션 아이템의 검토 경고 목록. 거절된 항목은 경고가 없습니다."""
    if action["review"] == "rejected":
        return []
    out = []
    if not action["assignee"]:
        out.append({"k": "owner", "t": "담당자 미정"})
    if not action["due"]:
        out.append({"k": "due", "t": "기한 미정"})
    if action.get("estimated"):
        out.append({"k": "evidence", "t": "근거 부족"})
    owner = member_by_id(members, action["assignee"])
    if owner and owner["load"] >= OVERLOAD:
        out.append({"k": "load", "t": "업무 과부하"})
    return out


def recommend(action: dict, meeting: dict, members: list[dict]) -> dict:
    """미지정 업무의 담당자 후보 Top-3.

    점수 = 스킬 적합도 40% + 부서·역할 40% + 업무 여유도 20%
    이번 회의에서 이미 맡은 업무 수도 업무량에 더해 한 사람에게 몰리지 않게 합니다.
    """
    need = action.get("need_skills") or []
    if not need:
        return {"hold": "업무에 필요한 스킬을 원문에서 판단하기 어려워 추천을 보류했어요. 담당자 확인이 필요합니다.", "items": []}

    extra: dict[str, int] = {}
    for a in (meeting.get("detail") or {}).get("actions", []):
        if a["assignee"] and a["review"] != "rejected":
            extra[a["assignee"]] = extra.get(a["assignee"], 0) + 1

    items = []
    for m in members:
        if m.get("no_recommend"):
            continue
        matched = [s for s in need if s in m["skills"]]
        skill = len(matched) / len(need)
        role = 1.0 if m["dept"] == action.get("need_dept") else (0.5 if matched else 0.3)
        load = m["load"] + extra.get(m["id"], 0)
        ease = max(0.0, min(1.0, 1 - load / 10))
        score = round((SCORE_WEIGHTS["skill"] * skill + SCORE_WEIGHTS["role"] * role + SCORE_WEIGHTS["ease"] * ease) * 100)
        reasons = []
        if matched:
            reasons.append("보유 스킬 일치: " + ", ".join(matched))
        if m["dept"] == action.get("need_dept"):
            reasons.append(m["dept"] + " 소속")
        reasons.append(f"진행 중 업무 {load}건" + (f" (이번 회의 {extra[m['id']]}건 포함)" if extra.get(m["id"]) else ""))
        items.append({
            "member": m, "score": score, "skill": round(skill * 100), "role": round(role * 100), "ease": round(ease * 100),
            "reasons": reasons, "attendee": m["id"] in meeting["attendees"],
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    top = items[:3]
    if top and top[0]["score"] < RECOMMEND_THRESHOLD:
        return {"hold": f"적합도가 기준({RECOMMEND_THRESHOLD}점)보다 낮아 추천을 보류했어요. 담당자 확인이 필요합니다.", "items": top}
    return {"hold": None, "items": top}
