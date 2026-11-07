# 추천 에이전트 평가

담당자 추천 에이전트([recommend_agent.py](../recommend_agent.py))가 액션아이템마다 필요한 스킬을 제대로 고르고, 추천을 안정적으로 내는지 점수로 확인한다.
정답표(이 업무의 정답 담당자)는 쓰지 않는다. 그래서 "맞는 사람을 골랐나"가 아니라 **실패 없이 추천하는지, 매번 비슷하게 추천하는지, 고른 스킬이 타당한지**를 본다.

> 이 테스트는 **실제 Neo4j와 실제 OpenAI를 호출한다.** 돌릴 때마다 API 비용이 들고 점수가 조금씩 바뀐다.
> 에이전트가 LLM을 `reasoning_effort="high"`로 부르므로(서비스와 같은 설정) 온보딩 평가보다 비용·시간이 더 든다.
> **결과는 DB에 저장하지 않는다.** `save`를 바꿔 끼워 메모리에만 담는다 (조회는 실제 DB).
> 가짜 LLM으로 로직(점수 계산·직급별 선택·재시도)만 검사하는 테스트는 `backend/tests/unit/test_recommend.py`에 따로 있다.

## 파일 구성

| 파일 | 하는 일 | 결과 |
|---|---|---|
| `conftest.py` | 실제 DB·LLM 연결, 저장 막기, 실행 시간 측정, 가짜 LLM | — |
| `test_quantitative.py` | 회의마다 2번씩 돌려 성공률·일관성 측정 | % 점수 |
| `test_scenarios.py` | 정해진 상황 6개에서 올바르게 동작하는지 | 통과/실패 |
| `test_qualitative.py` | LLM이 "고른 요구 스킬이 타당한가"를 채점 (deepeval GEval) | 0~1 점수 |

## 준비

1. **`remeet/.env`** 에 `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `OPENAI_API_KEY`, `OPENAI_MODEL`
2. **DB 데이터**: 평가할 회의가 Neo4j에 적재돼 있고, 그 회의에 **담당자가 없는 open 액션아이템**이 있어야 한다 (담당자가 정해진 건은 추천 대상이 아님). 사람(Person)·스킬(Skill)·팀·역할 데이터도 있어야 한다.
3. **평가할 회의 지정**: 기본값은 `m-1023,m-1030,m-1103,m-1106,m-1110` (playground 데이터 기준). 실제 DB와 다르면 환경변수로 바꾼다.
4. **패키지 설치** (`remeet` 폴더에서): `uv pip install pytest deepeval` (`deepeval`은 `test_qualitative.py`에만 필요)

## 실행

`remeet` 폴더에서 실행한다. **`-s`를 꼭 붙여야** 점수가 화면에 찍힌다.

PowerShell
```powershell
$env:PYTHONPATH = "backend/src"
$env:RECOMMEND_EVAL_MEETINGS = "m-1023,m-1030"   # 생략하면 기본값
uv run pytest backend/src/followup/analysis/recommend/tests -s
```

Git Bash
```bash
PYTHONPATH=backend/src RECOMMEND_EVAL_MEETINGS="m-1023,m-1030" \
  uv run pytest backend/src/followup/analysis/recommend/tests -s
```

- **온보딩 tests와 한 명령으로 같이 돌리지 않는다.** 두 폴더 모두 `conftest`라는 같은 이름의 파일을 import해서 한쪽이 오류 난다. 폴더별로 따로 실행한다.
- 비용을 아끼려면 `test_scenarios.py` → `test_qualitative.py` → `test_quantitative.py` 순으로 하나씩 돌리고, 회의 수를 1~2개로 줄인다.
  `test_quantitative.py`는 회의마다 2번 실행하므로 가장 비싸다.

## 결과 읽는 법

### test_quantitative.py

액션아이템마다 한 줄, 마지막에 전체 점수가 나온다.
```
m-1023-a1-t1: pending_manager_review skills=['sk-log', 'sk-pg'] source=llm recs=5 same_users=1.00 same_skills=1.00
...
Items: 8, success: 87.5%, error: 0.0%, llm skill: 100.0%, same users: 82.0%, same skills: 90.0%, mean latency per meeting: 21.40s
```
(숫자·ID는 예시)

| 항목 | 뜻 | 통과 기준 |
|---|---|---|
| success | 추천이 나온(`pending_manager_review`) 비율 | 80% 이상 |
| error | 처리 실패(`error`) 비율 | 5% 이하 |
| llm skill | 추천 성공 건 중 LLM이 스킬을 직접 고른 비율 (나머지는 비슷한 업무에서 빌려 옴) | 80% 이상 |
| same users / same skills | 같은 액션아이템을 2번 돌렸을 때 추천 인원·요구 스킬이 겹치는 정도 (Jaccard) | 둘의 평균 70% 이상 |
| mean latency per meeting | 회의 하나 처리 평균 시간 (액션아이템은 병렬 처리) | 기록만 함 |

액션아이템별 `status`:
- `pending_manager_review`: 추천 나옴 (관리자 확인 대기)
- `no_candidate`: 조건에 맞는 후보 없음
- `skill_unclear`: 스킬 목록에서 맞는 스킬을 못 고르고 빌릴 것도 없음
- `error`: 조회·LLM 실패 (저장 안 하고 다음 요청에 다시 처리)

통과 기준은 파일 맨 위 `MIN_*`, `MAX_*` 상수에서 바꾼다.

### test_scenarios.py

| 시나리오 | 만드는 상황 | 기대 동작 | 실제 LLM |
|---|---|---|---|
| s01 정상 | 첫 번째 회의 전체 | `error` 없음, 직급별 Top-2(최대 6명) | 호출 |
| s02 후보 없음 | 후보 조회가 빈 목록 | `no_candidate`, 추천 없음, 결과는 저장 | 호출 |
| s03 LLM 실패 | LLM이 연결 오류를 냄 | `error`, 저장 안 함 | 안 함 |
| s04 스킬 해석 실패 | LLM이 없는 스킬만 고르고, 비슷한 업무도 없음 | `skill_unclear`, 목록에 없는 필요 기술 기록 | 안 함 |
| s05 스킬 빌려오기 | s04 + 비슷한 업무가 있음 | 그 업무의 스킬을 빌려 추천 (`similar_item`) | 안 함 |
| s06 맥락 조회 실패 | 액션아이템 맥락 조회가 오류를 냄 | `error`, 저장 안 함, 경고에 "조회 실패" | 안 함 |

s02~s06은 첫 번째 회의의 액션아이템 하나로 돌린다. 그런 액션아이템이 없으면 `skipped`로 나온다.

### test_qualitative.py

첫 번째 회의에서 요구 스킬이 나온 액션아이템 3개(`SAMPLE`)를 LLM이 채점한다.
```
m-1023-a1-t1: 0.8 (…채점 이유…)
...
Skill relevance mean: 0.77
```
채점 기준:
1. 고른 스킬이 이 업무에 실제로 필요한가
2. 중요도(core/major/minor)가 업무 비중과 맞는가
3. 이유가 회의 맥락에 근거하고 구체적인가
4. 스킬 목록에 있는데 빠뜨린 핵심 스킬은 없는가

평균 0.7 이상이면 통과. 채점 모델은 `OPENAI_MODEL` (없으면 `gpt-4o-mini`).

## 점수가 기준보다 낮으면

점수는 테스트가 실패해도 먼저 찍힌다. 결과는 화면에만 나오고 파일로 저장되지 않는다.

- `평가할 액션아이템이 없음` → `RECOMMEND_EVAL_MEETINGS`의 회의가 DB에 있는지, 담당자 없는 open 액션아이템이 있는지 확인
- success가 낮고 `no_candidate`가 많음 → Person의 `position`(senior/mid/junior), 팀·역할·스킬 데이터가 비었는지 확인
- `skill_unclear`가 많거나 llm skill이 낮음 → DB 스킬 목록이 업무를 덮지 못함. 줄에 찍힌 액션아이템의 필요 기술을 보고 스킬 추가 검토
- same users / same skills가 낮음 → LLM이 실행마다 다른 스킬을 고름. 프롬프트(`prompts/interpret.md`) 점검
- `error`가 많음 → Neo4j 연결, OpenAI 키·요금 한도 확인

## 왜 reasoning_effort가 high인가

테스트는 `recommend_agent.LLM_EFFORT`를 그대로 쓴다 (서비스 `recommend_assignees.py`와 같은 값). 평가는 실제로 쓰는 설정으로 해야 의미가 있다.
추천 에이전트의 LLM은 업무를 읽고 스킬·중요도·대상 팀/역할을 **판단**하고, 그 판단이 바로 점수·순위에 들어간다. 그래서 질문 분류·문장 정리만 하는 온보딩 에이전트(`low`)보다 높게 잡혀 있다 (코드 주석: "playground에서 비교해 고른 값").
