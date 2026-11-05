# recommend_agent.py — 담당자 추천 에이전트 코드 설명

담당자가 정해지지 않은 액션아이템마다 회의 맥락을 읽고, 직급(senior / mid / junior)별 담당자 후보를 추천한다. 결과는 Neo4j에만 남기고(파일 없음) 관리자가 HITL 화면에서 검토한다. 에이전트는 추천만 남기고 배정(`ASSIGNED_TO`)은 하지 않는다.

## 실행

`ontology` 폴더에서 실행한다. `load.py`로 데이터를 적재한 뒤(`uv run python scripts/load.py --reset --effort low`), Neo4j가 켜져 있어야 한다.

```bash
uv run python scripts/recommend_agent.py              # 담당자 미결정 + 아직 추천 안 된 액션아이템
uv run python scripts/recommend_agent.py --force      # 이미 추천된 건도 다시 (pending 추천만 교체)
uv run python scripts/recommend_agent.py --self-check # 가짜 DB·LLM으로 그래프 전체 검사 (Neo4j, API 키 불필요)
uv run python scripts/recommend_agent.py --model gpt-6-luna --effort high   # 기본값
uv run python scripts/recommend_report.py luna_high   # 리포트만 다시 뽑기
```

실행이 끝나면:
- 비용을 `data/reports/cost_log.jsonl`에 한 줄 쌓는다 (`script: recommend_agent.py`, load.py와 같은 형식. 실패해도 그때까지 나간 호출은 기록).
- `data/reports/recommend_report/<label>/<YYYYMMDD-HHMM>.md` 리포트를 만든다 (`--label`, 기본 `모델 끝 이름_effort` 예: `luna_high`). 실행마다 새 파일. 한눈에 보기 표, 확인이 필요한 안내, 액션아이템별 요구 스킬·후보 점수표.
- `LANGSMITH_TRACING=true`면 LangGraph 실행 전체가 LangSmith에 `recommend_agent`라는 이름으로 남는다 (metadata: recommend_run_id, model, effort).

필요한 환경 변수 (`ontology/.env` 또는 저장소 루트 `.env`):

| 변수 | 용도 |
| --- | --- |
| `OPENAI_API_KEY` | interpret 노드의 LLM 호출 |
| `NEO4J_PASSWORD` | Neo4j 접속 (`load.py`와 같은 값) |
| `NEO4J_USERNAME` | 기본값 `neo4j` |
| `NEO4J_URI` | 기본값 `bolt://localhost:7687` |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | LangSmith 추적 (load.py와 같은 값) |

## 파일 구성

```
scripts/
├─ recommend_agent.py        에이전트 전체 (Neo4j 쿼리, 점수 계산, LangGraph 그래프, self-check)
├─ recommend_report.py       Neo4j 추천 결과 → data/reports/recommend_report/<label>/<YYYYMMDD-HHMM>.md
└─ prompts/
   └─ interpret.md           요구 조건 해석 노드의 system 프롬프트
```

### 점수 계산 섹션

`recommend_agent.py`의 "점수 계산" 섹션은 `junbyong/graph_rag_test/3_recommend_assignee.py`에서 필요한 함수만 그대로 옮긴 것이다 (AST 비교로 동일함을 확인).
- `normalize_spec`: LLM 해석 결과에서 catalog에 없는 ID를 걸러낸다. 스킬이 하나도 남지 않으면 `ValueError`를 낸다.
- `rank_candidates`: 점수를 계산해 순위를 매기고, 제외 대상을 이유와 함께 따로 돌려준다.
- 그 밖에 `skill_weights`, `skill_score`, `role_team_score`, `meets_requirement`, `workload_score`, `total_score`, `data_warnings`, `response_text`

원본의 예전 스키마(`Entity:User`)용 쿼리, 프롬프트, `main()`은 옮기지 않았다. 이제 원본과 연결돼 있지 않으므로, 원본의 점수 규칙이 바뀌어도 여기에는 자동으로 반영되지 않는다.

점수 기준:
- total = 스킬×0.5 + 역할·팀×0.2 + 업무량×0.3 (`WEIGHTS`)
- 목표 역할·팀이 없으면 total = (스킬×0.5 + 업무량×0.3) ÷ 0.8
- 스킬 = 100 × Σ(정규화된 중요도 × 숙련도 ÷ 4). 중요도는 core 0.5 / major 0.3 / minor 0.2 (`IMPORTANCE`)
- 역할·팀 = 직접 일치 100 / 인접 역할 또는 같은 팀 70 / 그 외 0
- 업무량 = 100 ÷ (1 + 미완료 담당 업무 수)
- 동점이면 스킬 점수 → 업무량 점수 → user_id 순
- 제외 대상: 비활성, 필수 역할·팀 불일치, 요구 스킬 미보유, 회의에서 명시된 담당자

## 그래프 구조

그래프는 두 개다. 바깥 **run 그래프**가 액션아이템 목록을 만들고, 안쪽 **item 그래프**가 한 건씩 처리한다.

```
[run 그래프]
load_items ──Send(액션아이템별 병렬)──▶ process_item ──▶ END
     └─ 미결정 액션아이템 없음 ──▶ END

[item 그래프] 액션아이템 1건
fetch_context ─┬─▶ interpret(LLM) ─▶ validate ─┬─▶ score ─▶ save (추천 / no_candidate)
               │        ▲                      │
               │        └──── 재시도 1회 ──────┤
               │                               ├─▶ borrow(유사 업무 스킬) ─┬─▶ score
               │                               │                         └─▶ give_up
               │                               └─▶ give_up ─▶ save (skill_unclear / error)
               └─▶ save (error: 맥락 조회 실패)
```

`error`는 저장하지 않는다. 추천 엣지도 `recommend_status`도 없으니 다음 실행 때 `ITEMS_QUERY`가 다시 고른다 (사용자가 재실행할 필요 없음).

`process_item`은 item 그래프를 `invoke`하고 결과를 `RunState.results`에 붙인다. `results`는 `Annotated[list, operator.add]`라서 병렬 branch의 결과가 합쳐진다. `process_item`은 예외를 잡아 `error` 결과로 바꾸므로(저장 안 함), 한 건이 실패해도 다른 건은 계속 처리된다.

### 노드별 동작

| 노드 | LLM | 하는 일 | 다음 노드 |
| --- | --- | --- | --- |
| `load_items` |  | catalog(스킬·역할·팀·사용자)와 미결정 액션아이템 ID 목록 조회 | ID마다 `Send` / 없으면 END |
| `fetch_context` |  | 액션아이템, 소주제(Discussion), 안건, Topic, 회의, 참석자 맥락과 후보 전원 조회 | 실패 시 `save`(error), 성공 시 `interpret` |
| `interpret` | ✔ | 맥락과 catalog로 spec(required_skills, 목표 팀·역할 등) 생성. 재시도 때는 직전 해석과 실패 이유를 `previous_attempt`로 넘김 | `validate` |
| `validate` |  | `normalize_spec`으로 검증 | 성공 시 `score` / 실패 + 시도 2회 미만이면 `interpret` / 스킬을 못 골랐으면 `borrow` / LLM 호출 실패면 `give_up` |
| `borrow` |  | 스킬 목록에 맞는 스킬이 없음 → LLM이 적은 `unmatched_skills`를 `missing_skills`로 남김. 이름이 가장 비슷한 다른 액션아이템(difflib)의 `REQUIRES_SKILL`(빌려 온 것 제외)을 빌리고 `borrowed_from`에 그 ID를 남김 | 빌렸으면 `score`, 아니면 `give_up` |
| `give_up` |  | LLM 호출 실패였으면 `error`, 스킬을 못 찾았으면 `skill_unclear` | `save` |
| `score` |  | `rank_candidates`로 순위 계산 → `first_tier`로 tier 표시 → `select_by_position`으로 직급별 선발. 0명이면 `no_candidate` | `save` |
| `save` |  | `build_result`로 저장할 값 생성 → Neo4j 저장 (error면 저장 안 함). warnings는 터미널에만 출력 | END |

### 실패 처리

| 상황 | 결과 |
| --- | --- |
| 요구 스킬을 catalog에서 못 찾음 | interpret 1회 재시도 → 유사 업무 스킬 빌리기 → 그래도 없으면 `skill_unclear` (저장, 다음 실행에서 건너뜀) |
| LLM 호출 실패 | OpenAI 클라이언트가 3번 재시도(`LLM_RETRIES`) × interpret 2회 → 그래도 실패하면 `error` (저장 안 함, 다음 실행에 다시) |
| 맥락·후보 조회 실패, 액션아이템 맥락 없음 | `error` (저장 안 함) |
| 추천 0명 | `no_candidate` (저장, 다음 실행에서 건너뜀) |
| Neo4j 저장 실패 | `error` (트랜잭션이라 아무것도 안 남음, 다음 실행에 다시) |

맥락 없음처럼 데이터 자체가 잘못된 건은 몇 번을 다시 해도 실패한다. 터미널 출력과 LangSmith로 확인한다.

## 주요 함수

| 함수 | 설명 |
| --- | --- |
| `first_tier(attendee_ids, candidates)` | 1차 풀(tier 1) = 회의 참석자 + 참석자와 같은 팀 사람. 나머지는 tier 2 |
| `select_by_position(ranked)` | 직급별 2명(최대 6명)을 tier 1에서 순위대로 먼저 채우고, 모자란 자리만 tier 2(팀 외)로 채운다(warning). 리스트 순서가 직급 안 `rank`다. 부족한 직급은 비워 두고 warnings에 남긴다. 직급이 없거나 잘못된 사람은 선발에서 뺀다 |
| `call_json(llm, prompt, payload)` | LLM을 JSON 모드로 호출하고, 결과가 JSON 객체가 아니면 예외를 낸다 |
| `build_result(state)` | item 그래프 state로 저장할 값(`recommendations`, `required_skills`, `missing_skills`, `borrowed_from`, `status`)을 만든다 |
| `build_graph(tools, llm)` | run/item 그래프를 만든다. DB 접근은 `tools` 딕셔너리로 주입받는다 |
| `run_once(graph, force)` | 그래프를 실행하고 결과 목록을 돌려준다 (LangSmith metadata에 `recommend_run_id`) |
| `neo4j_tools(driver)` | 실제 Neo4j용 `tools`. 노드가 병렬로 돌기 때문에 세션을 공유하지 않는다 (`driver.execute_query`, 저장은 호출마다 새 세션) |
| `self_check()` | `FakeLLM`과 가짜 `tools`로 그래프 전체를 검사한다 |

`tools`를 주입받는 구조라서 같은 그래프가 실제 Neo4j와 self-check의 가짜 DB에서 똑같이 돈다.

| tools 키 | 반환 |
| --- | --- |
| `catalog()` | `{"skills": [{id, name}], "roles": [...], "teams": [...], "users": [...]}` |
| `items(force)` | 처리할 action_item_id 목록 |
| `context(item_id)` | 맥락 dict, 없으면 `None` |
| `candidates()` | `(후보 목록, ASSIGNED_TO 데이터 존재 여부)` |
| `save(result)` | Neo4j 저장 (`build_result`의 값) |

## Neo4j 쿼리

LLM은 Cypher를 쓰지 않는다. 모든 쿼리는 코드에 고정돼 있고, 검증된 ID만 파라미터로 넘긴다.

| 쿼리 | 내용 |
| --- | --- |
| `CATALOG_QUERIES` | Skill / Role / Team / Person의 id·name (Skill은 description 포함) |
| `ITEMS_QUERY` | `status='open'`이고 `ASSIGNED_TO`가 없는 ActionItem. `--force`가 없으면 `RECOMMENDED_FOR`나 `recommend_status`가 있는 건 제외 |
| `CONTEXT_QUERY` | ActionItem → Discussion(소주제 이름·논의 문장) → Agenda → Meeting → Project(이름 포함), Topic, 참석자. LLM에는 `llm_context()`가 ActionItem, Discussion(title, discussions), Agenda(title, background)만 골라 보낸다 |
| `CANDIDATE_QUERY` | Person 전원의 팀, 역할, 스킬(level), 미완료 담당 업무 수. `rank_candidates`가 기대하는 모양(`teams[].id`, `skills[].id`)으로 맞춰 돌려준다 |
| `CLEAR_PENDING` | 해당 액션아이템의 `pending` 추천만 삭제 (selected / not_selected는 유지) |
| `SAVE_RECOMMENDATIONS` | ActionItem에 `recommend_status`(성공이면 null = 속성 삭제), `missing_skills`를 기록하고 `RECOMMENDED_FOR(status='pending')` 생성 |
| `SAVE_REQUIRED_SKILLS` | 해당 액션아이템의 `REQUIRES_SKILL {source:'llm'|'similar_item'}`과 `BORROWED_SKILLS_FROM`을 지우고 다시 만든다 |
| `RESET_QUERIES` | `--reset`: 이 스크립트가 만든 엣지·속성 전부 삭제. 예전 구조의 속성(`recommend_run_id` 등)이 남아 있으면 같이 지운다 |

`CLEAR_PENDING`, `SAVE_RECOMMENDATIONS`, `SAVE_REQUIRED_SKILLS`는 한 트랜잭션에서 실행된다.

## Neo4j에 저장되는 것

파일은 만들지 않는다.

| 위치 | 속성 | 뜻 |
| --- | --- | --- |
| `(Person)-[:RECOMMENDED_FOR]->(ActionItem)` | `status` | `pending`(검토 대기) / `selected` / `not_selected` (뒤의 둘은 HITL 백엔드가 바꿈) |
| | `score`, `skill_score`, `role_team_score`, `workload_score` | 총점과 축별 점수. 목표 역할·팀이 없으면 `role_team_score`는 없음 |
| | `rank` | 같은 직급 안 순서 (1 또는 2). tier 1 우선이라 점수 순과 다를 수 있다 |
| `ActionItem` | `recommend_status` | `no_candidate`(조건에 맞는 사람 없음) / `skill_unclear`(요구 스킬을 못 정함). 추천 엣지가 있으면 속성 없음 |
| | `missing_skills` | LLM이 필요하다고 봤지만 스킬 목록에 없는 기술 이름 (스킬 목록 업데이트 신호) |
| `(ActionItem)-[:REQUIRES_SKILL]->(Skill)` | `importance`, `reason`, `source` | 중요도, 이 업무에 왜 필요한지, `llm` / `similar_item`(빌려 옴) |
| `(ActionItem)-[:BORROWED_SKILLS_FROM]->(ActionItem)` | | 유사 업무에서 스킬을 빌렸을 때만. 빌려 온 액션아이템 |

추천 엣지는 직급별 Top-2(최대 6명)에게만 생긴다. 직급 순서는 `Person.position`, 선행 업무 담당자는 `FOLLOWS_UP`→`ASSIGNED_TO`로 조회한다.

## 테스트

- **self-check** (`--self-check`): 아래 경우를 assert로 확인한다.
  - 재시도 후 성공, `skill_unclear`, LLM 실패 `error`, 맥락 없음 `error`, `no_candidate`. `error`는 저장하지 않음
  - 직급별 2명 선발(tier 1 우선, 모자란 자리만 tier 2)과 `rank`(점수가 더 높은 팀 외 후보가 2등), 부족한 직급을 다른 직급으로 채우지 않음, 직급 없는 사람 제외 경고
  - 비활성·스킬 없음 제외, 유사 업무 스킬 차용(`borrowed_from`), `missing_skills`, 저장 값의 키 목록
- **현재 구조 확인** (2026-11-04, 로컬 Neo4j, LLM 호출 없음): `load.py`로 적재한 뒤 open 액션아이템 14건 모두 `CONTEXT_QUERY`로 맥락 조회 성공, 후보 48명 조회 성공
- **실제 DB 테스트** (2026-11-03, 로컬 Neo4j + 실제 LLM, 예전 구조·더미 데이터 기준)
  - 첫 실행: 액션아이템 8건 모두 `pending_manager_review`, `RECOMMENDED_FOR` 13개 생성
  - 옵션 없이 다시 실행: 이미 추천된 건을 모두 건너뜀
  - 점수 함수를 옮긴 뒤 `--force` 실행: 8건 모두 `pending_manager_review`. 이전 pending 추천은 지워지고 새 run의 13개로 교체됨
- **원본 비교**: 옮긴 상수·함수 17개를 `junbyong/graph_rag_test/3_recommend_assignee.py`와 AST로 비교해 모두 동일함을 확인

## 알아둘 점

- 사람은 48명이다. 회의 참석자 4명(users.json의 `meeting_attendees`)과 추천 테스트용 가상 인물이다.
- `load.py`는 회의록에 담당자가 명시된 경우(`assign_source:'meeting_explicit'`)와 선행 업무(`FOLLOWS_UP`) 담당자가 있는 경우(`assign_source:'history'`, 선행 담당자가 활성일 때만)에만 `ASSIGNED_TO`를 만든다. 이 둘은 추천 대상에서 빠진다.
- `owner_raw`는 회의록 `assignee` 원문이다 (예: "UI 디자이너", 담당자가 없으면 속성 없음). 회의 참석자의 `Person.name`은 A~D라서 이름으로는 맞지 않고, load.py의 `assign_explicit_owners()`가 쉼표로 나눈 각 이름을 참석자의 이름 또는 역할명과 맞춰 `ASSIGNED_TO {assign_source:'meeting_explicit'}`로 바로 잇는다. 모두 맞으면 추천 대상에서 빠지고, 하나라도 못 맞추면 추천을 돌리며 warning을 남긴다.
- 액션만 들어 있는 소주제에서 나온 액션아이템은 맥락의 `discussion`이 빈 배열이다 (`load.py`가 액션 문장을 `Discussion.discussions[]`에 넣지 않는다). 이때는 안건의 배경·결정사항이 주된 맥락이다.
- LLM 해석은 실행마다 조금씩 달라질 수 있다. `--force` 재실행 때 8건 중 3건이 달라졌다 (예: m-1110-a1-t1에서 s-promo 중요도가 major → minor). 점수 계산은 같은 spec이면 항상 같은 결과를 낸다.
- 관리자 확정(`ASSIGNED_TO` 생성, `RECOMMENDED_FOR.status`를 selected / not_selected로 변경)은 HITL 백엔드에서 구현할 부분이고, 이 코드에는 없다.
## 선행 업무 담당자 (FOLLOWS_UP)

- 연결은 `load.py`의 `link_follows_up()`이 회의를 적재할 때 한다. 이번 회의 액션 전부와 같은 프로젝트 이전 회의 액션(완료 포함)을 LLM에 한 번에 보여 주고, 액션당 선행 업무 0~1개를 고른다. 후보에 없는 ID는 버린다. LLM이 실패해도 회의 적재는 계속된다.
- 추천은 선행 업무 담당자를 따로 다루지 않는다. 웹이나 리포트가 `(ActionItem)-[:FOLLOWS_UP]->()<-[:ASSIGNED_TO]-(Person)`으로 조회해서 보여 준다.
