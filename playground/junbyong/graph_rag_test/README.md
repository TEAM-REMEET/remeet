# 구성원 그래프 · 담당자 추천

CSV 5개로 구성원·팀·역할·스킬 정보를 Neo4j에 등록하고, 그 그래프를 바탕으로
질의응답과 담당자 추천 점수 계산을 하는 스크립트 모음입니다.

```
CSV 5개 ──1_import_csv.py──▶ Neo4j ──┬─ 2_ask_csv_graph.py ─▶ 구성원 질의응답 (콘솔)
                                     └─ 3_recommend_assignee.py ─▶ 추천 결과 (콘솔 + recommend_result.json)
```

| 파일 | 역할 | Neo4j | LLM |
|---|---|---|---|
| `1_import_csv.py` | CSV 검증 후 노드·관계 등록 | 쓰기 | 사용 안 함 |
| `2_ask_csv_graph.py` | 등록된 구성원 정보에 대한 한국어 질의응답 | 읽기 | 사용 |
| `3_recommend_assignee.py` | 업무 문장 → 요구 조건 해석 → 점수 계산 → 추천 | 읽기 | 사용 (끌 수 있음) |
| `users.csv` 외 4개 | 입력 데이터 | | |
| `recommend_result.json` | 3번 실행 결과 (실행할 때마다 덮어씀) | | |

`src2/`는 이전 회의록 그래프 예제(`1_test_connection.py`, `2_build_graph.py`, `3_ask_graph.py`)이며
이 문서의 흐름과는 별개입니다.

---

## 1. 준비

프로젝트 최상위 폴더의 `.env`에 접속 정보를 넣습니다 (`.env_example` 참고).

```
OPENAI_API_KEY=...
NEO4J_URI=neo4j://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
# 선택
NEO4J_DATABASE=neo4j
OPENAI_MODEL=gpt-5.5
```

- `.env`는 `src/.env` → 최상위 `.env` 순서로 읽습니다. 다른 파일을 쓰려면 `--env-file 경로`.
- 값이 없으면 `NEO4J_URI=neo4j://localhost:7687`, `NEO4J_USERNAME=neo4j`, `NEO4J_DATABASE=neo4j`,
  `OPENAI_MODEL=gpt-5.5`를 씁니다.
- 패키지는 최상위 `requirements.txt` (neo4j, python-dotenv, langchain-openai).

## 2. 실행 순서

```bash
cd src

# ① CSV 검사만 (DB 접속 없음)
python 1_import_csv.py --dry-run

# ② Neo4j 등록
python 1_import_csv.py

# ③ 질의응답 (선택)
python 2_ask_csv_graph.py -q "Bart의 팀과 스킬은?"

# ④ 담당자 추천
python 3_recommend_assignee.py --task "다음 회의 전까지 리모컨 시장 트렌드를 조사해서 팀에 공유해 주세요"
```

---

## 3. CSV 파일

모두 UTF-8(엑셀의 BOM 포함 UTF-8도 가능)이며, **헤더 이름과 순서가 정확히 일치**해야 합니다.

### teams.csv
| 컬럼 | 설명 |
|---|---|
| `team_id` | 팀 ID (예: `team:TS3003`) |
| `name` | 팀 이름 |
| `is_active` | `true` / `false` |

### roles.csv
| 컬럼 | 설명 |
|---|---|
| `role_id` | 역할 ID (예: `role:ME`) |
| `name` | 역할 이름 (예: 마케팅 전문가) |

### skills.csv
| 컬럼 | 설명 |
|---|---|
| `skill_id` | 스킬 ID (예: `skill:marketing_trend_research`) |
| `name` | 스킬 이름 |
| `aliases` | 다른 이름들, `\|`로 구분 (예: `트렌드 조사\|마케팅 조사`). LLM이 업무 문장과 스킬을 연결할 때 참고 |

### users.csv
| 컬럼 | 설명 |
|---|---|
| `user_id` | 사용자 ID (예: `person:TS3003:D`) |
| `name` | 이름 |
| `is_active` | `true` / `false`. `false`면 추천에서 제외 |
| `position` | 직급, 비워도 됨 |
| `team_id` | `teams.csv`의 `team_id` |
| `role` | `roles.csv`의 `role_id` |
| `email` | 비워도 됨 |
| `created_at` | `YYYY-MM-DD` |

### user_skills.csv — 사용자와 스킬을 잇는 연결 테이블
| 컬럼 | 설명 |
|---|---|
| `user_id` | `users.csv`의 `user_id` |
| `skill_id` | `skills.csv`의 `skill_id` |
| `level` | 숙련도 1~4 (아래 표) |
| `updated_at` | `YYYY-MM-DD` |

| level | 의미 |
|---|---|
| 1 (하) | 기초 지식이 있음 |
| 2 (중) | 가이드가 있으면 수행할 수 있음 |
| 3 (상) | 혼자서 수행할 수 있음 |
| 4 (최상) | 숙련되어 다른 사람을 도울 수 있음 |

### 검증 규칙 (`1_import_csv.py`가 DB에 쓰기 전에 전부 검사)
- ID와 이름은 필수. **ID는 모든 테이블을 통틀어 중복될 수 없음** (모든 노드가 `Entity.id` 하나를 키로 씀).
- `users.team_id`, `users.role`, `user_skills.user_id`, `user_skills.skill_id`는 실제로 존재하는 ID여야 함.
- 같은 `(user_id, skill_id)` 쌍이 두 번 나오면 오류.
- 하나라도 틀리면 아무것도 저장하지 않습니다.

> 현재 CSV는 TS3003 회의록 기반 **테스트 데이터**입니다. `level`은 검증 전 추론값이고 `is_active`는 테스트용 설정입니다.

---

## 4. Neo4j에 만들어지는 그래프

```
(:Entity:User:Person:TeamMember) -[:MEMBER_OF]-> (:Entity:Team)
                                 -[:HAS_ROLE]---> (:Entity:Role)
                                 -[:HAS_SKILL {level}]-> (:Entity:Skill)
```

| 관계 | 만드는 근거 | 관계 속성 |
|---|---|---|
| `MEMBER_OF` | `users.csv`의 `team_id` | `source_type` |
| `HAS_ROLE` | `users.csv`의 `role` | `source_type` |
| `HAS_SKILL` | `user_skills.csv`의 행 1개 = 관계 1개 | `level`, `proficiency`, `updated_at`, `verification_status='inferred_unverified'`, `source_file`, `source_type` |

- 노드는 `MERGE (n:Entity {id})`로 만들고, ID 문자열이 같은 노드끼리 연결합니다 (이름으로 매칭하지 않음).
- 모든 노드·관계에 `source_type = 'user_db_csv'`가 붙습니다. 2·3번 스크립트는 이 값으로 데이터를 찾습니다.
- 다시 등록하면 CSV에 있는 사용자의 `user_db_csv` 관계를 지우고 새로 만듭니다 (CSV에서 뺀 스킬은 DB에서도 사라짐).
  다른 출처의 노드·관계는 건드리지 않습니다.

---

## 5. 담당자 추천 (`3_recommend_assignee.py`)

### 흐름
```
① 회의 업무 문장
② LLM: 필요한 스킬·역할 해석     → DB에 있는 ID만 남기고 나머지는 "확인 필요"로
③ Neo4j: 후보와 근거 조회        → 스킬 숙련도, 팀·역할, 진행 중 업무 수
④ Python: 점수 계산·순위 결정    → LLM은 점수에 관여하지 않음
⑤ LLM: 추천 이유·부족한 정보 설명
⑥ 관리자 검토                    → recommend_result.json 저장, DB에는 배정을 쓰지 않음
```

### 제외 조건 (점수 계산 전)
| 상태 | 조건 |
|---|---|
| `explicit` | 회의에서 담당자로 명시된 사람 — 기록만 하고 추천 대상에서 제외 |
| `excluded` | 비활성 팀원 (`is_active=false`) |
| `excluded` | 역할·팀이 **필수 조건**인 업무에서 조건에 맞지 않는 사람 |

### 점수 공식
```
전체 = 스킬×0.5 + 역할·팀×0.2 + 업무량×0.3
전체 = (스킬×0.5 + 업무량×0.3) ÷ 0.8        ← 목표 역할·팀이 없는 업무
```

**스킬 점수** = 100 × Σ(정규화된 중요도 × 숙련도 ÷ 4)
- 중요도: 핵심(core) 0.5, 주요(major) 0.3, 보조(minor) 0.2 → 합이 1.0이 되도록 정규화
- 해당 스킬이 없으면 숙련도 0
- 예: SQL 핵심·4, 통계 주요·2, 대시보드 보조·1 → 100 × (0.5×1 + 0.3×0.5 + 0.2×0.25) = **70점**

**역할·팀 점수** (목표 역할·팀이 있을 때만)
| 조건 | 점수 |
|---|---|
| 목표 역할(과 팀)에 직접 일치 | 100 |
| 인접 역할이거나, 역할은 맞지만 다른 팀이거나, 같은 팀이지만 역할이 다름 | 70 |
| 관련 없음 | 0 |

인접 역할은 DB에 정보가 없어서 ②단계 LLM이 판단합니다.

**업무량 점수** = 100 ÷ (1 + 미완료 업무 수)
- 0건 100, 1건 50, 2건 약 33, 3건 25
- `ASSIGNED_TO`로 연결된 `ActionItem` 중 `work_status ≠ work_completed`이고 `status ≠ completed`인 업무를 셉니다
  (`action_item_id` 중복 제거).
- ⚠️ 현재 DB에는 `ActionItem`/`ASSIGNED_TO`가 없어서 **모든 후보가 100점**입니다. 결과의 "확인 필요"에 표시됩니다.

**동점 처리**: 스킬 점수 → 업무량 점수 → user_id 순.

### 실행 옵션
| 옵션 | 설명 |
|---|---|
| `--task "문장"` / `-t` | 업무 문장. LLM이 요구 조건을 해석 |
| `--spec 파일.json` | LLM 해석 대신 요구 조건을 직접 지정 (`--task`와 둘 중 하나) |
| `--top N` | 추천 후보 수 (기본 3) |
| `--no-explain` | ⑤ LLM 설명 생략 |
| `--show-spec` | ② 해석 결과(요구 조건) 출력 |
| `--json-out 경로` | 결과 저장 위치 (기본 `recommend_result.json`, 상대 경로는 이 폴더 기준) |
| `--model`, `--env-file` | 모델명, .env 경로 지정 |

`--spec`과 `--no-explain`을 함께 쓰면 **LLM 없이** 실행됩니다 (API 키 불필요).

### spec 파일 예시
```json
{
  "task": "리모컨 시장 트렌드 조사 후 다음 회의에서 공유",
  "required_skills": [
    {"skill_id": "skill:marketing_trend_research", "importance": "core"},
    {"skill_id": "skill:project_coordination", "importance": "minor"}
  ],
  "target_team_id": null,
  "target_role_ids": ["role:ME"],
  "adjacent_role_ids": ["role:UI"],
  "role_team_required": false,
  "explicit_assignee_ids": []
}
```
`required_skills`만 필수이고 나머지 키는 생략할 수 있습니다. DB에 없는 ID는 무시되고 "확인 필요"에 기록됩니다.
요구 스킬이 하나도 유효하지 않으면 추천을 중단합니다.

### 콘솔 출력 예
```
순위  이름 (ID)                          전체   스킬  역할·팀  업무량(미완료)
  1   화자 D (person:TS3003:D)              76.8   53.6     100   100.0(0)
  2   Bart (person:TS3003:A)              51.1   14.3      70   100.0(0)
  3   화자 B (person:TS3003:B)              44.0    0.0      70   100.0(0)
```
역할·팀 점수가 생략된 업무에서는 `-`로 표시됩니다.

---

## 6. 결과 JSON (`recommend_result.json`)

| 키 | 내용 |
|---|---|
| `task` | 업무 문장 |
| `spec` | 검증을 거친 요구 조건 (위 spec 구조 + `unmatched_skills`, `notes`) |
| `recommendations` | 상위 `--top`명. 아래 표 참고 |
| `other_candidates` | 상위권 밖 후보 수 |
| `excluded` | 제외된 사람: `user_id`, `name`, `status`(`explicit`/`excluded`), `reason` |
| `warnings` | 관리자가 확인할 점 (DB에 없는 스킬, 업무량 데이터 없음, 숙련도 미검증 등) |
| `status` | 항상 `pending_manager_review` — 확정 배정이 아님 |
| `explanation` | ⑤ LLM 설명. `--no-explain`이거나 설명 생성에 실패하면 키가 없음 |

`recommendations[]` 항목:

| 키 | 내용 |
|---|---|
| `rank` | 순위 |
| `user_id`, `name`, `teams`, `roles` | 후보 정보 |
| `total` | 전체 점수 |
| `skill_score`, `role_team_score`, `workload_score` | 항목별 점수. `role_team_score`는 목표 역할·팀이 없으면 `null` |
| `open_tasks` | 미완료 업무 수 |
| `skill_evidence[]` | 요구 스킬별 `skill_id`, `importance`(핵심/주요/보조), `weight`(정규화된 중요도), `level`(0이면 미보유), `verification_status` |

---

## 7. 알아둘 점

- **추천은 후보 제안**입니다. 최종 배정은 관리자가 결정하며, 스크립트는 DB에 배정을 기록하지 않습니다.
- LLM은 ② 해석과 ⑤ 설명만 합니다. Cypher는 모두 코드에 고정되어 있고 LLM이 만든 쿼리는 실행하지 않습니다.
- 점수의 한계: 역할·팀 점수는 스킬 점수와 겹칠 수 있고, 업무 수는 실제 작업량을 거칠게만 나타냅니다.
- `SOURCE = "user_db_csv"` 값은 세 스크립트에 각각 들어 있으므로 바꿀 때는 모두 같이 바꿔야 합니다.
