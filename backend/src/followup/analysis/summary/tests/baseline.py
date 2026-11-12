"""베이스라인: 그래프(생성 → 검증 ⇄ 재수정) · 규칙 프롬프트 · 출력 스키마 없이 LLM 한 번 호출.

입력(발화 번호가 붙은 원문 · 회의 정보)만 에이전트와 같다. 출력은 구조화 출력(LLMMeetingSummary · 태그 목록) 대신
자유 형식 마크다운으로 받고, 지표에 필요한 것(안건 제목 · 항목 문장 · 근거 발화 번호)만 읽어
test_quantitative · test_qualitative가 쓰는 모양(active_agendas · iter_items · evidence_utterance_indices)으로 맞춘다.
"""
import re
import time
from types import SimpleNamespace

from followup import settings as cfg
from followup.analysis.summary import steps
from followup.analysis.summary.postprocess import parse_meeting_info
from followup.analysis.summary.preprocessing import load_transcript
from followup.analysis.summary.prompts import extract_summary_action_item as generate_prompt

PROMPT = """너는 회의록 작성 도우미다. 입력된 회의 원문(발화마다 번호가 붙어 있음)을 안건별로 요약한다.
각 안건에 논의 · 결정 · 액션아이템 항목을 쓰고, 항목마다 근거가 된 발화 번호를 단다.
원문에 없는 내용은 쓰지 않는다.
안건은 '## 안건 제목' 줄로, 항목은 '- 내용 [근거 발화 번호, ...]' 줄로 쓴다."""

_NUMS = re.compile(r"\[([\d,\s~\-]+)\]\s*$")


def _evidence(line: str, valid: set[int]) -> tuple[str, list[int]]:
    """줄 끝 [3, 5, 7~9] → 문장, 발화 번호 (원문에 없는 번호는 버림 — 에이전트 후처리와 같음)"""
    m = _NUMS.search(line)
    if not m:
        return line, []
    nums = []
    for part in m.group(1).split(","):
        bounds = [int(x) for x in re.findall(r"\d+", part)]
        if len(bounds) == 2:            # 5~6 / 5-6
            nums += range(bounds[0], bounds[1] + 1)
        else:
            nums += bounds
    return line[:m.start()].strip(), sorted({n for n in nums if n in valid})


def parse(markdown: str, valid: set[int]) -> list:
    agendas = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            agendas.append(SimpleNamespace(agenda_number=len(agendas) + 1, agenda_title=line.lstrip("#").strip(), items=[]))
        elif re.match(r"^([-*]|\d+\.)\s+", line):
            if not agendas:
                agendas.append(SimpleNamespace(agenda_number=1, agenda_title="(안건 없음)", items=[]))
            text, ev = _evidence(re.sub(r"^([-*]|\d+\.)\s+", "", line), valid)
            agendas[-1].items.append(SimpleNamespace(text=text, evidence_utterance_indices=ev))
    for a in agendas:
        ev = [i for it in a.items for i in it.evidence_utterance_indices]
        a.evidence_range = [min(ev), max(ev)] if ev else []
        a.iter_items = (lambda items: lambda: iter(items))(a.items)
    return [a for a in agendas if a.items]


def run(meeting_id: str, text: str, llm) -> SimpleNamespace:
    start = time.perf_counter()
    loaded = load_transcript(meeting_id, text)
    user = generate_prompt.build_user_prompt(steps._utterances(loaded), loaded["llm_metadata"])
    kwargs = {} if cfg.LLM_TEMPERATURE is None else {"temperature": cfg.LLM_TEMPERATURE}
    response = llm._get_client().responses.create(model=cfg.model_name(), instructions=PROMPT, input=user,
                                                  reasoning={"effort": cfg.REASONING_EFFORT}, **kwargs)
    agendas = parse(response.output_text, {u.utterance_index for u in loaded["transcript"]})
    summary = SimpleNamespace(
        active_agendas=lambda: agendas, transcript=loaded["transcript"],
        meeting_info=parse_meeting_info(loaded["metadata"], loaded.get("participants"), loaded.get("speakers")),
        verification=SimpleNamespace(status="N/A"), raw=response.output_text)
    return SimpleNamespace(summary=summary, latency_sec=time.perf_counter() - start, llm_calls=1)


def _self_check() -> None:
    md = "## 가격\n- 25유로로 결정 [3, 5~6, 99]\n- 근거 없음\n## 빈 안건\n"
    (a,) = parse(md, set(range(1, 10)))
    assert a.agenda_title == "가격" and a.evidence_range == [3, 6]
    assert [it.evidence_utterance_indices for it in a.iter_items()] == [[3, 5, 6], []]


_self_check()
