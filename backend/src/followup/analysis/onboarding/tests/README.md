# 온보딩 에이전트 평가

"회의에 물어보기"(온보딩 에이전트, [onboarding_agent.py](../onboarding_agent.py))가 질문에 맞는 근거를 찾아 답하는지 점수로 확인한다.

> 이 테스트는 **실제 Neo4j와 실제 OpenAI를 호출한다.** 돌릴 때마다 API 비용이 들고, LLM 응답이 달라서 점수가 조금씩 바뀐다.
> 가짜 LLM으로 로직만 검사하는 테스트는 `backend/tests/unit/test_ask.py`에 따로 있다 (비용 없음).

## 파일 구성

| 파일 | 하는 일 | 결과 |
|---|---|---|
| `eval_questions.json` | 평가 질문 22개와 정답 안건 ID | — |
| `conftest.py` | 실제 DB·LLM 연결, 질문 실행 + 근거 조회 횟수·응답 시간 측정 | — |
| `test_quantitative.py` | 22문항 전체로 정확도 측정 | % 점수 |
| `test_scenarios.py` | 정해진 상황 4개에서 올바르게 동작하는지 | 통과/실패 |
| `test_qualitative.py` | LLM이 답변을 채점 (deepeval) | 0~1 점수 |

## 준비

1. **`remeet/.env`** 에 아래 값이 있어야 한다.
   - `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`
   - `OPENAI_API_KEY`, `OPENAI_MODEL`
   - 요약 결과 폴더가 기본 위치가 아니면 `SUMMARY_OUTPUT_DIR` (원문 발화 근거를 여기서 읽는다)
2. **DB 데이터**: Neo4j에 TS3003 회의(a~d)가 적재돼 있어야 한다.
   결정(Decision) 노드는 앱에서 "회의에 물어보기"를 한 번 하면 자동으로 만들어진다. 없으면 결정 질문 점수가 낮게 나온다.
3. **패키지 설치** (`remeet` 폴더에서)
   ```
   uv pip install pytest deepeval
   ```
   `deepeval`은 `test_qualitative.py`에만 필요하다.

## 실행

`remeet` 폴더에서 실행한다. **`-s`를 꼭 붙여야** 점수가 화면에 찍힌다 (없으면 통과/실패만 나옴).

PowerShell
```powershell
$env:PYTHONPATH = "backend/src"
uv run pytest backend/src/followup/analysis/onboarding/tests -s
```

Git Bash
```bash
PYTHONPATH=backend/src uv run pytest backend/src/followup/analysis/onboarding/tests -s
```

하나만 돌릴 때는 파일 경로를 지정한다.
```
uv run pytest backend/src/followup/analysis/onboarding/tests/test_quantitative.py -s
```
비용을 아끼려면 `test_scenarios.py` → `test_quantitative.py` → `test_qualitative.py` 순으로 하나씩 돌린다.
`test_quantitative.py`는 질문 22개, `test_qualitative.py`는 질문 1개 + 채점 호출 여러 번이다.

## 결과 읽는 법

### test_quantitative.py

문항마다 한 줄, 마지막에 전체 점수가 나온다.
```
D1: intent=decision status=grounded cited=['TS3003c_a2', 'TS3003d_a1'] 4.2s
...
Intent: 86.4%, recall: 78.0%, no_evidence: 95.5%, tool use: 100.0%, mean latency: 5.10s
```
(숫자는 예시)

| 항목 | 뜻 | 통과 기준 |
|---|---|---|
| Intent | 질문 유형(decision/history/action/overview/people/skill)을 맞게 분류한 비율 | 80% 이상 |
| recall | 정답 안건 중 답변 출처(`sources`)에 들어간 비율. 정답 안건이 없는 문항은 제외 | 70% 이상 |
| no_evidence | "근거 없음" 판정이 맞은 비율 (없는 내용엔 없다고, 있는 내용엔 답해야 함) | 90% 이상 |
| tool use | 근거 조회를 1번 이상 한 비율 | 90% 이상 |
| mean latency | 평균 응답 시간 | 기록만 함 |

문항별 줄의 `status`는 `grounded`(근거 있음) / `partial`(근거 없는 문장 일부 제거) / `no_evidence` / `error` 중 하나다.
통과 기준은 파일 맨 위 `MIN_*` 상수에서 바꾼다.

### test_scenarios.py

| 시나리오 | 질문 | 기대 동작 |
|---|---|---|
| s01 정상 질문 | D1 "버튼과 화면 인터페이스는 어떻게 하기로 했어요?" | 근거를 찾고, 정답 안건이 출처에 있음 |
| s02 인사말 | "안녕하세요!" | 유형 `other`, 근거 조회 0번 |
| s03 없는 내용 | N1 "사내 복지 포인트 정책은…" | `no_evidence`, 출처 없음 |
| s04 검색 실패 | D1 + 근거 조회 함수가 전부 에러를 내도록 바꿈 | `no_evidence`, 경고에 "근거 조회 실패" |

점수 없이 `4 passed` / `1 failed …` 로 나온다.

### test_qualitative.py

D3 "음성 인식 기능은 왜 빠졌어요?"의 답변을 LLM이 채점한다.
```
Relevance: 0.85 (…채점 이유…)
Faithfulness: 0.9 (…채점 이유…)
```
| 항목 | 뜻 | 통과 기준 |
|---|---|---|
| Relevance | 답변이 질문에 맞는 내용인가 | 0.7 이상 |
| Faithfulness | 답변이 인용한 근거(논의 문장·발화 원문)에 있는 내용만 말하는가 | 0.7 이상 |

채점 모델은 `OPENAI_MODEL` (없으면 `gpt-4o-mini`).

## 점수가 기준보다 낮으면

점수는 테스트가 실패해도 먼저 찍힌다. 결과는 화면에만 나오고 파일로 저장되지 않는다.

- 문항별 줄에서 `cited`가 정답과 다른 문항을 찾는다. 정답 ID는 `eval_questions.json`의 `expected_agenda_ids`.
- 결정 질문만 낮으면 → DB에 Decision 노드가 있는지 확인 (준비 2번).
- 거의 전부 `no_evidence` → DB에 TS3003 회의가 없거나 안건 ID가 `eval_questions.json`과 다름.
- `test_qualitative.py`에서 "답변에 원문 근거가 붙어야 함" 실패 → `SUMMARY_OUTPUT_DIR`에 요약 결과 파일(`<meeting_id>/<meeting_id>_summary.json`)이 있는지 확인.
- `NEO4J_PASSWORD가 설정되지 않았습니다` → `remeet` 폴더에서 실행했는지, `.env`가 있는지 확인.

## 질문 추가

`eval_questions.json`에 한 줄 추가한다 (아래 질문·ID는 형식 예시).
```json
{"id": "D6", "intent": "decision", "question": "가격은 얼마로 정했어요?",
 "expected_agenda_ids": ["TS3003b_a1"], "expect_no_evidence": false}
```
- `intent`: 정답 질문 유형
- `expected_agenda_ids`: 답변 출처에 나와야 하는 안건 ID (특정할 수 없으면 `[]`, recall 계산에서 빠짐)
- `expect_no_evidence`: 회의에 없는 내용이라 "근거 없음"이 정답이면 `true`
