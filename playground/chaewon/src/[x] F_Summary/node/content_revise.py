"""[4] 특정 문장 재수정

내용 검증 이슈(content_issues)를 반영해 지적된 항목만 고침 (지적되지 않은 항목은 그대로)
    ① 삭제: 코드 처리 (LLM 호출 없음) → status="DELETED"
    ② 수정 · 병합 · 추가: 안건별 LLM 1회 (LLMRevisionResult)
       요청 ID = 수정 · 병합: 대상 항목 ID / 추가: {agenda_id}_new{n} (임시 ID, 반영 시 add_item으로 실제 ID 부여)
    ③ 코드 검사 후 요청 ID로 항목에 반영
       - 수정: 문장 · 근거 · 태그 · 소주제 갱신
       - 병합: 대상 항목을 합친 내용으로 갱신, 합쳐진 항목은 DELETED
       - 추가: 지정된 섹션에 새 항목 추가
       - 근거: 원본 발화 안의 번호만 사용 (없으면 수정 · 병합은 기존 근거, 추가는 이슈의 판단 근거)
       - 태그: 섹션에 맞지 않으면 수정 · 병합은 기존 태그 유지, 추가는 반영하지 않음
       수정 전 값은 모두 항목 history에 남김 (stage="content_revise")
    ④ 결과 처리
       - 반영된 이슈: resolved=True, revised_item_id 기록
       - 수정 · 병합 · 추가된 항목 ID → recheck_item_ids ([5] 재검증 대상)
       - 반영하지 못한 이슈 (LLM 누락 · 검사 실패): 대상 항목 REVIEW_REQUIRED

노트북 확인용: revise_agenda(...)로 안건 하나씩 실행 가능
LangGraph 노드: content_revise_node(state) → 다음 노드는 항상 content_validation
"""

import config as cfg
from llm import call_structured
from node.content_validation import mark_review_required, source_utterances
from prompt.content_revise_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas import SECTION_TAGS, Agenda, ContentIssue, LLMRevisedItem, LLMRevisionResult, Segment, SummaryState
from tracing import traceable

# 사용자 설정: config.py — STEP_EFFORT["content_revise"]

# ── 내부 상수 ────────────────────────────────────────
STAGE = "content_revise"             # 항목 history에 남길 단계 이름


# ── ② 요청 ID ─────────────────────────────────────────
def request_ids(issues: list[ContentIssue]) -> dict[str, ContentIssue]:
    # 수정 · 병합: 대상 항목 ID / 추가: {agenda_id}_new{n}
    requests, n = {}, 0
    for issue in issues:
        if issue.action == "추가":
            n += 1
            requests[f"{issue.agenda_id}_new{n}"] = issue
        else:
            requests[issue.target_item_id] = issue
    return requests


# ── ③ 코드 검사 · 반영 ────────────────────────────────
def segments_of(evidence: list[int], segments: list[Segment], default: list[int]) -> list[int]:
    # 근거 발화가 핵심 발화에 속한 Segment (없으면 default)
    ids = sorted({s.segment_id for s in segments for i in evidence if s.utterance_start <= i <= s.utterance_end})
    return ids or list(default)


def apply_revision(agenda: Agenda, issue: ContentIssue, rev: LLMRevisedItem, valid: set[int],
                   segments: list[Segment], loop: int, log: list[str]) -> str | None:
    # 반영한 항목 ID 반환, 반영하지 못하면 None
    where = f"{agenda.agenda_id} {issue.issue_id}({issue.action} {issue.target_item_id})"
    reason = f"[{issue.criterion}] {issue.reason}"
    text = rev.revised_text.strip()
    if not text:
        log.append(f"{where}: 수정 문장이 비어 있음 → 반영 안 함")
        return None

    evidence = sorted({i for i in rev.evidence_utterance_indices if i in valid})
    if len(evidence) < len(set(rev.evidence_utterance_indices)):
        log.append(f"{where}: 원본 발화에 없는 근거 번호 제거 {sorted(set(rev.evidence_utterance_indices) - valid)}")
    tag_ok = rev.tag in SECTION_TAGS[issue.section]
    if not tag_ok:
        log.append(f"{where}: {issue.section}에 맞지 않는 태그 '{rev.tag}'")
    subtopic = rev.subtopic.strip() if issue.section == "major_discussions" else ""

    if issue.action == "추가":
        if not tag_ok:
            log.append(f"{where}: 태그 오류로 추가 안 함")
            return None
        evidence = evidence or sorted(i for i in issue.evidence_utterance_indices if i in valid)
        if not evidence:
            log.append(f"{where}: 근거 발화가 없어 추가 안 함")
            return None
        fields = {"text": text, "evidence_utterance_indices": evidence,
                  "source_segment_ids": segments_of(evidence, segments, agenda.source_segment_ids)}
        if issue.section != "discussion_background":
            fields["tag"] = rev.tag
        if issue.section == "major_discussions":
            fields["subtopic"] = subtopic
        item = agenda.add_item(issue.section, **fields)
        log.append(f"{where}: {item.item_id} 추가")
        return item.item_id

    item = agenda.find_item(issue.target_item_id)
    if item is None or item.status != "ACTIVE":
        log.append(f"{where}: 대상 항목이 ACTIVE가 아님 (다른 이슈에서 삭제 · 병합됨) → 반영 안 함")
        return None

    merged = []
    if issue.action == "병합":
        merged = [r for i in issue.related_item_ids if (r := agenda.find_item(i)) is not None and r.status == "ACTIVE"]
        if len(merged) < len(issue.related_item_ids):
            log.append(f"{where}: 이미 삭제 · 병합된 항목은 병합에서 제외")
        if not evidence:
            evidence = sorted({i for it in [item, *merged] for i in it.evidence_utterance_indices})
    if not evidence:
        evidence = item.evidence_utterance_indices
        log.append(f"{where}: 유효한 근거가 없어 기존 근거 유지")

    item.revise(text, evidence, STAGE, loop, reason)
    if tag_ok and hasattr(item, "tag"):
        item.tag = rev.tag
    if issue.section == "major_discussions" and subtopic:
        item.subtopic = subtopic
    for other in merged:
        other.delete(STAGE, loop, f"{item.item_id}에 병합 — {reason}")
        item.source_segment_ids = sorted({*item.source_segment_ids, *other.source_segment_ids})
    log.append(f"{where}: 반영" + (f" (병합으로 삭제: {[o.item_id for o in merged]})" if merged else ""))
    return item.item_id


# ── 안건 하나 재수정 ──────────────────────────────────
@traceable(name="content_revise")
def revise_agenda(agenda: Agenda, issues: list[ContentIssue], segments: list[Segment],
                  utterance_texts: dict[int, str], meeting_id: str, loop: int) -> dict:
    # agenda를 직접 수정함 (노드에서는 복사본을 넘김)
    log: list[str] = []
    utterances = source_utterances(agenda, segments, utterance_texts)
    valid = {i for i, _ in utterances}

    requests = request_ids([i for i in issues if i.action != "삭제"])
    user_prompt, llm_out, revisions = None, None, {}
    if requests:
        user_prompt = build_user_prompt(agenda, requests, utterances)      # 반영 전 상태로 프롬프트 작성
        llm_out = call_structured(SYSTEM_PROMPT, user_prompt, LLMRevisionResult,
                                  tag=f"{meeting_id}_content_revise_{agenda.agenda_id}_loop{loop}", effort=cfg.STEP_EFFORT["content_revise"])
        for rev in llm_out.revision_result:
            if rev.target_item_id not in requests:
                log.append(f"{agenda.agenda_id}: 요청에 없는 ID {rev.target_item_id} → 제외")
            elif rev.target_item_id in revisions:
                log.append(f"{agenda.agenda_id}: {rev.target_item_id} 결과 중복 → 첫 결과만 사용")
            else:
                revisions[rev.target_item_id] = rev
    request_of = {issue.issue_id: rid for rid, issue in requests.items()}

    revised: dict[str, str] = {}        # issue_id → 반영된 항목 ID
    for issue in issues:                # 이슈 순서대로 반영
        if issue.action == "삭제":
            item = agenda.find_item(issue.target_item_id)
            if item is None or item.status != "ACTIVE":
                log.append(f"{agenda.agenda_id} {issue.issue_id}(삭제 {issue.target_item_id}): 이미 삭제 · 병합됨")
                continue
            item.delete(STAGE, loop, f"[{issue.criterion}] {issue.reason}")
            log.append(f"{agenda.agenda_id} {issue.issue_id}(삭제 {issue.target_item_id}): 삭제")
            revised[issue.issue_id] = item.item_id
            continue
        rev = revisions.get(request_of[issue.issue_id])
        if rev is None:
            log.append(f"{agenda.agenda_id} {issue.issue_id}({issue.action} {issue.target_item_id}): LLM 결과 없음")
            continue
        item_id = apply_revision(agenda, issue, rev, valid, segments, loop, log)
        if item_id is not None:
            revised[issue.issue_id] = item_id

    recheck = [revised[i.issue_id] for i in issues if i.issue_id in revised and i.action != "삭제"]
    recheck = [i for i in dict.fromkeys(recheck) if agenda.find_item(i).status == "ACTIVE"]
    return {
        "revised": revised,             # issue_id → 반영된 항목 ID
        "recheck_item_ids": recheck,    # [5] 재검증 대상
        "user_prompt": user_prompt,     # LLM 입력 (삭제만 있으면 None)
        "llm_output": llm_out,          # LLM 원본 출력
        "log": log,                     # 반영 · 제외 내용
    }


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="content_revise_node")
def content_revise_node(state: SummaryState) -> dict:
    loop = state.get("content_loop", 0)                 # 이슈가 나온 검증 회차
    pending = state.get("content_issues", [])
    agendas = [a.model_copy(deep=True) for a in state["agendas"]]

    revised: dict[str, str] = {}
    recheck: list[str] = []
    for agenda in agendas:
        issues = [i for i in pending if i.agenda_id == agenda.agenda_id]
        if not issues:
            continue
        result = revise_agenda(agenda, issues, state["segments"], state["utterance_texts"], state["meeting_id"], loop)
        revised.update(result["revised"])
        recheck += result["recheck_item_ids"]
        print(f"[재수정 {loop}회차] {agenda.agenda_id} {agenda.agenda_title}: 이슈 {len(issues)}개 중 "
              f"{len(result['revised'])}개 반영 / 재검증 대상 {result['recheck_item_ids']}")
        for line in result["log"]:
            print("   -", line)

    failed = [i for i in pending if i.issue_id not in revised]
    marked = mark_review_required(agendas, failed)
    if marked:
        print(f"[재수정] 반영하지 못한 이슈 → REVIEW_REQUIRED 표시: {marked}")

    issue_log = []
    for issue in state.get("content_issue_log", []):
        if issue.issue_id in revised:
            issue = issue.model_copy(update={"resolved": True, "revised_item_id": revised[issue.issue_id]})
        issue_log.append(issue)

    return {
        "agendas": agendas,
        "content_issues": [],
        "content_issue_log": issue_log,
        "recheck_item_ids": recheck,                # [5] 재검증 대상
    }
