# recommend_graph.py — 담당자 추천 에이전트 코드 설명

담당자가 정해지지 않은 액션아이템마다 회의 맥락을 읽고, 직급(senior / mid / junior)별 담당자 후보를 추천한다. 결과는 관리자가 HITL 화면에서 검토한다. 에이전트는 추천만 남기고 배정(`ASSIGNED_TO`)은 하지 않는다.

## 실행

`ontology` 폴더에서 실행한다. `load.py`로 데이터를 적재한 뒤, Neo4j가 켜져 있어야 한다.

```bash
uv run python scripts/recommend_graph.py              # 담당자 미결정 + 아직 추천 안 된 액션아이템
uv run python scripts/recommend_graph.py --force      # 이미 추천된 건도 다시 (pending 추천만 교체)
uv run python scripts/recommend_graph.py --self-check # 가짜 DB·LLM으로 그래프 전체 검사 (Neo4j, API 키 불필요)
uv run python scripts/recommend_graph.py --model gpt-5.5
```

필요한 환경 변수 (`ontology/.env` 또는 저장소 루트 `.env`):

| 변수 | 용도 |
| --- | --- |
| `OPENAI_API_KEY` | interpret / explain 노드의 LLM 호출 |
| `NEO4J_PASSWORD` | Neo4j 접속 (사용자 이름은 `neo4j` 고정, `load.py`와 같음) |
| `NEO4J_URI` | 기본값 `bolt://localhost:7687` |
| `OPENAI_MODEL` / `LLM_MODEL` | 모델 이름. 둘 다 없고 `--model`도 없으면 `gpt-5.5` |

## 파일 구성

```
scripts/
├─ recommend_graph.py        에이전트 전체 (Neo4j 쿼리, 점수 계산, LangGraph 그래프, 출력 JSON 생성, self-check)
├─ prompts/
│  ├─ interpret.md           요구 조건 해석 노드의 system 프롬프트
│  └─ explain.md             추천 설명 노드의 system 프롬프트
└─ recommend_results/        결과 JSON (실행 시 생성)
```

### 점수 계산 섹션

`recommend_graph.py`의 "점수 계산" 섹션은 `junbyong/graph_rag_test/3_recommend_assignee.py`에서 필요한 함수만 그대로 옮긴 것이다 (AST 비교로 동일함을 확인).
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
fetch_context ─┬─▶ interpret(LLM) ─▶ validate ─┬─▶ score ─┬─▶ explain(LLM) ─▶ save
               │        ▲                      │          └─▶ save (no_candidate)
               │        └──── 재시도 1회 ──────┤
               │                               └─▶ give_up ─▶ save (skill_unclear / error)
               └─▶ save (error: 맥락 조회 실패)
```

`process_item`은 item 그래프를 `invoke`하고 결과를 `RunState.results`에 붙인다. `results`는 `Annotated[list, operator.add]`라서 병렬 branch의 결과가 합쳐진다. `process_item`은 예외를 잡아 `error` 결과로 바꾸므로, 한 건이 실패해도 다른 건은 계속 처리된다.

### 노드별 동작

| 노드 | LLM | 하는 일 | 다음 노드 |
| --- | --- | --- | --- |
| `load_items` |  | catalog(스킬·역할·팀·사용자)와 미결정 액션아이템 ID 목록 조회 | ID마다 `Send` / 없으면 END |
| `fetch_context` |  | 액션아이템, 안건, Topic, 회의, 참석자 맥락과 후보 전원 조회 | 실패 시 `save`(error), 성공 시 `interpret` |
| `interpret` | ✔ | 맥락과 catalog로 spec(required_skills, 목표 팀·역할 등) 생성. 재시도 때는 직전 해석과 실패 이유를 `previous_attempt`로 넘김 | `validate` |
| `validate` |  | `normalize_spec`으로 검증 | 성공 시 `score` / 실패 + 시도 2회 미만이면 `interpret` / 아니면 `give_up` |
| `give_up` |  | LLM 호출 실패였으면 `error`, 스킬을 못 찾았으면 `skill_unclear` | `save` |
| `score` |  | `rank_candidates`로 순위 계산 → `select_by_position`으로 직급별 선발 | 메인 추천이 있으면 `explain`, 0명이면 `save`(no_candidate) |
| `explain` | ✔ | 후보별 추천 이유, 요약, 확인 사항 생성. 후보 풀 밖 user_id의 reason은 버림 | `save` |
| `save` |  | 출력 JSON 생성 → Neo4j 저장 → 파일 저장 | END |

### 실패 처리

| 상황 | 결과 |
| --- | --- |
| 요구 스킬을 catalog에서 못 찾음 | interpret 1회 재시도 → 그래도 실패하면 `skill_unclear` |
| LLM 해석 호출 실패 | 1회 재시도 → 그래도 실패하면 `error` |
| 맥락·후보 조회 실패, 액션아이템 맥락 없음 | `error` |
| 메인 추천 0명 | `no_candidate` |
| LLM 설명 실패 | 점수 결과는 그대로 저장, warnings에 기록 |
| Neo4j 저장 실패 | JSON 파일은 저장, warnings에 기록 |

## 주요 함수

| 함수 | 설명 |
| --- | --- |
| `select_by_position(ranked)` | 전체 순위에서 직급별 상위 2명(최대 6명)을 뽑고, 메인 추천(senior 1, mid 1, junior 2)을 정한다. 부족한 직급은 비워 두고 warnings에 남긴다. 직급이 없거나 잘못된 사람은 선발에서 뺀다 |
| `scores_of(spec, c)` | `rank_candidates`와 같은 공식으로 한 사람의 점수를 계산한다. 제외된 사람에게 참고용 점수를 붙이는 데 쓴다 |
| `call_json(llm, prompt, payload)` | LLM을 JSON 모드로 호출하고, 결과가 JSON 객체가 아니면 예외를 낸다 |
| `build_output(state)` | item 그래프 state로 담당자 지정 모달용 JSON을 만든다. 실패 건도 같은 모양으로 만든다 |
| `recommend_rows(output)` | 출력 JSON에서 `RECOMMENDED_FOR` 관계 속성 목록(후보 풀 최대 6명)을 뽑는다 |
| `build_graph(tools, llm, out_dir)` | run/item 그래프를 만든다. DB 접근은 `tools` 딕셔너리로 주입받는다 |
| `run_once(graph, force, out_dir)` | `run_id`를 만들어 그래프를 실행하고, 결과가 있으면 `run_{run_id}.json`을 저장한다 |
| `neo4j_tools(driver)` | 실제 Neo4j용 `tools`. 노드가 병렬로 돌기 때문에 세션을 공유하지 않는다 (`driver.execute_query`, 저장은 호출마다 새 세션) |
| `self_check()` | `FakeLLM`과 가짜 `tools`로 그래프 전체를 검사한다 |

`tools`를 주입받는 구조라서 같은 그래프가 실제 Neo4j와 self-check의 가짜 DB에서 똑같이 돈다.

| tools 키 | 반환 |
| --- | --- |
| `catalog()` | `{"skills": [{id, name}], "roles": [...], "teams": [...], "users": [...]}` |
| `items(force)` | 처리할 action_item_id 목록 |
| `context(item_id)` | 맥락 dict, 없으면 `None` |
| `candidates()` | `(후보 목록, ASSIGNED_TO 데이터 존재 여부)` |
| `save(item_id, run_id, status, warnings, rows)` | Neo4j 저장 |

## Neo4j 쿼리

LLM은 Cypher를 쓰지 않는다. 모든 쿼리는 코드에 고정돼 있고, 검증된 ID만 파라미터로 넘긴다.

| 쿼리 | 내용 |
| --- | --- |
| `CATALOG_QUERIES` | Skill / Role / Team / Person의 id·name |
| `ITEMS_QUERY` | `status='open'`이고 `ASSIGNED_TO`가 없는 ActionItem. `--force`가 없으면 `RECOMMENDED_FOR`가 있는 건 제외 |
| `CONTEXT_QUERY` | ActionItem → AgendaItem → Meeting → Project, Topic(상위 Topic 포함), 참석자 |
| `CANDIDATE_QUERY` | Person 전원의 팀, 역할, 스킬(level), 미완료 담당 업무 수와 그 업무의 회의 ID. `rank_candidates`가 기대하는 모양(`teams[].id`, `skills[].id`)으로 맞춰 돌려준다 |
| `CLEAR_PENDING` | 해당 액션아이템의 `pending` 추천만 삭제 (accepted / rejected는 유지) |
| `SAVE_RECOMMENDATIONS` | ActionItem에 `recommend_status`, `recommend_run_id`, `recommended_at`, `recommend_warnings`를 기록하고 `RECOMMENDED_FOR(status='pending')` 생성 |

`CLEAR_PENDING`과 `SAVE_RECOMMENDATIONS`는 한 트랜잭션에서 실행된다.

## 출력

- `recommend_results/{action_item_id}.json`: 담당자 지정 모달용. 화면은 점수를 계산하지 않고 이 값을 그대로 표시한다.
- `recommend_results/run_{run_id}.json`: 이번 실행의 전체 결과
- 미결정 액션아이템이 없으면 아무 파일도 만들지 않는다.

출력 JSON의 주요 키:

| 키 | 내용 |
| --- | --- |
| `action_item` | 액션아이템과 회의·안건·Topic 맥락 |
| `recommendation` | `run_id`, `status`, `explanation`, `manager_checks`, `warnings` |
| `score_axes` | 점수 막대 3개의 라벨·가중치·적용 여부 (`applied: false`이면 화면에 "–") |
| `required_skills`, `unmatched_skills` | LLM이 해석한 요구 스킬, catalog에 없는 스킬 이름 |
| `main_recommendations` | 메인 카드 (`order`, `slot`, `user_id`). 순서는 senior → mid → junior |
| `candidates_by_position` | 직급별 후보 풀 user_id |
| `teams` | 팀별 구성원. 추천 후보 → 지정 가능 → 총점 순으로 정렬 |
| `people` | 제외된 사람을 포함한 전원의 정보 (`eligible`, `excluded_reason`, `scores`, `skills[].matched`, `reason` 등) |

`status`는 `pending_manager_review`, `no_candidate`, `skill_unclear`, `error` 중 하나다.

## 테스트

- **self-check** (`--self-check`): 아래 경우를 assert로 확인한다.
  - 재시도 후 성공, `skill_unclear`, LLM 실패 `error`, 맥락 없음 `error`, `no_candidate`
  - 직급별 상위 2명 선발, 부족한 직급을 다른 직급으로 채우지 않음, 직급 없는 사람 제외 경고
  - 비활성 제외, 참석 여부, 이번 회의 업무 수, 후보 풀 밖 reason 제거, 팀 구성원 정렬
  - `RECOMMENDED_FOR` 행 수, 결과 파일 목록, 미결정 건이 없으면 아무것도 쓰지 않음
- **실제 DB 테스트** (2026-11-03, 로컬 Neo4j + 실제 LLM)
  - 첫 실행: 액션아이템 8건 모두 `pending_manager_review`, `RECOMMENDED_FOR` 13개 생성
  - 옵션 없이 다시 실행: 이미 추천된 건을 모두 건너뜀
  - 점수 함수를 옮긴 뒤 `--force` 실행: 8건 모두 `pending_manager_review`. 이전 pending 추천은 지워지고 새 run의 13개로 교체됨
- **원본 비교**: 옮긴 상수·함수 17개를 `junbyong/graph_rag_test/3_recommend_assignee.py`와 AST로 비교해 모두 동일함을 확인

## 알아둘 점

- 지금 더미 데이터는 사람이 6명뿐이라 직급별 2명을 채우지 못하고, 메인 추천이 1\~2명에 그친다.
- `load.py`의 `assign_owner`가 주석 처리돼 있어 `ASSIGNED_TO`가 없다. 그래서 모든 open 액션아이템이 추천 대상이 되고, 업무량 점수가 전원 100점이다.
- `load.py`가 `owner_raw`를 저장하지 않아 맥락의 `owner_raw`는 항상 `null`이다.
- LLM 해석은 실행마다 조금씩 달라질 수 있다. `--force` 재실행 때 8건 중 3건이 달라졌다 (예: m-1110-a1-t1에서 s-promo 중요도가 major → minor). 점수 계산은 같은 spec이면 항상 같은 결과를 낸다.
- 메인 카드는 slot 순서라서, 총점이 더 높은 mid 후보가 senior 후보보다 뒤에 나올 수 있다 (`overall_rank`로 확인 가능).
- 관리자 확정(`ASSIGNED_TO` 생성, `RECOMMENDED_FOR.status`를 accepted / rejected로 변경)은 HITL 백엔드에서 구현할 부분이고, 이 코드에는 없다.