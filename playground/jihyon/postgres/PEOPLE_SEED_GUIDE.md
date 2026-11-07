# users.json → PostgreSQL 적재 · 이후 추가·수정 반영 방식

2026-11-07 · 대상: 사람·팀·스킬 데이터를 PostgreSQL과 연동하는 팀원

## 1. 요약

- `backend/seed/users.json`·`skills.json` 하나로 **Neo4j와 PostgreSQL을 둘 다** 채웁니다.
  - Neo4j: 기존 `backend/src/followup/entrypoints/seed_people.py` (변경 없음)
  - PostgreSQL: 새 `playground/jihyon/postgres/seed_people.py`
- 처음 한 번은 **시드**(`seed_people.py`)로 넣고, 이후 웹에서 추가·수정한 사람은 **`crud/people.py`의 `sync_people()`**로 PostgreSQL에 반영합니다 (6장).
- 비밀번호는 PostgreSQL에 넣지 않습니다. 계정은 만들어지지만 비밀번호 로그인은 안 됩니다.
- 지금은 playground에 있고, 나중에 backend로 합칠 예정입니다 (7장).

```
backend/seed/users.json ─┬─▶ backend seed_people.py        ─▶ Neo4j  (Person·Team·Role·Skill)
backend/seed/skills.json ┘                                          
                         └─▶ jihyon/postgres/seed_people.py ─▶ PostgreSQL (teams·roles·skills·accounts·people·person_skills)
```

## 2. 새로 생긴 표

| 표 | 키 | 칸 | Neo4j 대응 |
| --- | --- | --- | --- |
| `teams` | team_id | name, is_active | `Team` |
| `roles` | role_id | name, team_id → teams | `Role` |
| `skills` | skill_id | name, description, domain, tools(배열), source, ncs_code, ncs_name | `Skill` |
| `people` | user_id → accounts | name, position(junior/mid/senior), team_id, role_id, is_active, updated_at | `Person` + `MEMBER_OF` + `HAS_ROLE` |
| `person_skills` | (user_id, skill_id) | level(1~5), updated_at | `HAS_SKILL {level, updated_at}` |
| `accounts` (기존) | user_id | email, role(USER/ADMIN), password_hash(NULL) | Person의 email |

- 로그인 정보(`accounts`)와 프로필(`people`)을 나눴습니다. 관리 계정은 `accounts`에만 있고 `people`에는 없습니다. 그래서 담당자 추천이나 사람 목록에 섞이지 않습니다.
- 두 DB의 같은 사람·팀은 **같은 ID**(`user_id`, `team_id` …)로 이어집니다.
- 정의: [`schema.sql`](schema.sql)(새 DB), [`migrations/004_people.sql`](migrations/004_people.sql)(기존 DB에 추가)

## 3. users.json 칸 → 표

| users.json / skills.json | 들어가는 곳 |
| --- | --- |
| `teams[]` | `teams` |
| `roles[]` | `roles` |
| `skills.json[]` | `skills` (`tools`의 `"Jira\|Notion"`은 `["Jira","Notion"]` 배열로) |
| `people[].user_id, email` | `accounts` (role = `USER`) |
| `people[].name, position, team_id, role_id, is_active` | `people` |
| `people[].skills[]` (skill_id, level) | `person_skills` |
| `admins[]` | `accounts`만 (role = `ADMIN`) |
| `people[].password_hash`, `admins[].password_hash` | **넣지 않음** (4장) |
| `meeting_attendees` | **넣지 않음** (회의 참석은 Neo4j `ATTENDED` 담당) |

현재 데이터 기준으로 teams 4, roles 12, skills 38, accounts 49(구성원 48 + 관리자 1), people 48, person_skills 199행입니다.

## 4. 넣는 규칙

| 규칙 | 내용 | 이유 |
| --- | --- | --- |
| 미리 검사 | 없는 팀·직무·스킬, 직급 값, 레벨 1~5, user_id·이메일 중복을 DB에 쓰기 전에 검사 | DB 오류보다 어느 사람이 틀렸는지 바로 보이게 |
| 한 트랜잭션 | 전부 성공해야 커밋. 하나라도 실패하면 아무것도 남지 않음 | 반쯤 들어간 상태 방지 |
| 넣는 순서 | teams → roles → skills → accounts → people → person_skills | 외래 키 때문 |
| upsert | `INSERT … ON CONFLICT … DO UPDATE`. 다시 돌려도 중복 없음 | Neo4j `MERGE`와 같은 효과 |
| 기존 계정 보존 | `accounts`는 `ON CONFLICT (user_id) DO NOTHING`. 이미 있는 계정의 이메일·권한은 그대로 | `seed.py` 데모 계정의 로그인 이메일을 덮어쓰지 않게 |
| 재적재 거부 | `people`에 사람이 이미 있으면 멈춤. `--force`일 때만 파일 값으로 덮어씀 | 웹에서 고친 팀·스킬이 파일 값으로 되돌아가지 않게 (backend와 같은 규칙) |
| 비밀번호 제외 | `password_hash`는 NULL | users.json은 `scrypt$…`, PostgreSQL 쪽 로그인(`crud/accounts.py`)과 제약(migration 001)은 `pbkdf2_sha256$…`만 받음 |
| DB 주소 필수 | `DATABASE_URL`이 없으면 쓰지 않고 멈춤 | 엉뚱한 DB에 쓰지 않게 (`migrate.py`와 같은 규칙) |

## 5. 실행

`playground/jihyon/postgres` 폴더에서 실행합니다.

```powershell
docker compose up -d                                   # PostgreSQL 켜기
$env:DATABASE_URL="postgresql://remeet:remeet@127.0.0.1:5432/remeet"

python seed_people.py --check      # DB 없이 파일만 검사 → [검사 통과] teams 4, roles 12, …
python migrate.py                  # 기존 DB면 표 추가 (004_people.sql)
python seed_people.py              # 적재 → [적재 완료] …
python seed_people.py --force      # 이미 있어도 덮어쓰기 (웹 수정분 유실 주의)
```

- 새 DB라면 `python seed.py`(schema.sql로 전체 생성) 뒤에 `migrate.py`를 실행합니다.
- 테스트(DB 필요 없음): `python -m unittest discover -s tests -p "test_*.py" -v` (통합 테스트는 `TEST_DATABASE_URL`이 있을 때만 실행)

## 6. 이후 추가·수정할 때: `sync_people()`

시드 뒤에는 users.json을 다시 쓰지 않습니다. 웹에서 사람을 추가·수정하면 backend API가 Neo4j에 저장하고, **같은 결과를 `sync_people()`로 PostgreSQL에 반영**합니다.

### 6-1. 흐름

```
관리자 화면 ─▶ POST /api/people, PATCH /api/people/{id}   (backend routes/people.py)
                 │ 1) people.create_person / update_person  → Neo4j 저장
                 │      user_id(u{번호})와 새 직무의 role_id는 여기서 정해짐
                 │      저장된 사람(dict)을 돌려줌
                 │ 2) sync_people([그 사람])                → PostgreSQL 반영
                 ▼
               응답
```

- **Neo4j가 먼저인 이유:** user_id와 새 직무 ID를 Neo4j가 만들기 때문입니다. PostgreSQL은 그 결과를 그대로 받아 적습니다.
- 추가, 수정, 비활성화(`is_active: false`), 스킬 교체 모두 **같은 함수 하나**로 처리합니다. 새로 추가된 사람인지 수정된 사람인지 구분할 필요가 없습니다.

### 6-2. `sync_people(people)`이 하는 일

[`crud/people.py`](crud/people.py) · 입력은 backend `people.get_person()`이 돌려주는 모양의 사람 목록입니다.

| backend Person 칸 | 반영되는 곳 | 방식 |
| --- | --- | --- |
| user_id, email, permission | `accounts` | upsert. `permission: admin`이면 role = `ADMIN`, 아니면 `USER`. 이메일·권한은 Neo4j 값으로 덮어씀 |
| role_id, job, team_id | `roles` | 없을 때만 추가. 웹에서 새로 만든 직무는 그 사람의 팀으로 기록 |
| name, position, team_id, role_id, is_active | `people` | upsert. `updated_at` 갱신 |
| skills[] (skill_id, level) | `person_skills` | **목록 전체 교체.** 목록에 없는 스킬은 지우고, 있는 것은 upsert (backend와 같은 규칙) |

- 쓰기 전에 이메일·팀·직무·이름·직급이 비어 있지 않은지 검사합니다. 비어 있으면 `ValidationError`가 나고 아무것도 쓰지 않습니다.
- 여러 명을 넘기면 한 트랜잭션으로 처리합니다. 한 명이라도 실패하면 전부 취소됩니다.
- 권한 검사는 하지 않습니다. backend API가 `require_admin`으로 이미 막은 뒤 부르는 함수입니다.

### 6-3. backend에 붙이는 방법 (합칠 때)

[`backend/src/followup/entrypoints/api/routes/people.py`](../../../backend/src/followup/entrypoints/api/routes/people.py)의 두 API에서, Neo4j 저장이 끝난 직후 한 줄씩 부릅니다.

```python
@router.post("/people", ...)
def create_person(body: PersonCreate):
    try:
        person = people.create_person(body.model_dump())   # 1) Neo4j
    except ValueError as e:
        raise _bad_request(e) from e
    pg_people.sync_people([person])                        # 2) PostgreSQL
    return person

@router.patch("/people/{user_id}", ...)
def update_person(user_id: str, body: PersonUpdate):
    ...                                                    # 기존 코드 그대로 (person is None이면 404)
    pg_people.sync_people([person])                        # 404가 아닐 때만
    return person
```

backend에는 아직 PostgreSQL 연결(`db.py`)과 `crud/people.py`가 없어서, 이 둘을 backend 쪽으로 옮겨야 위 코드가 동작합니다.

### 6-4. 지켜야 할 것

1. **사람을 바꾸는 길은 이 두 API뿐이어야 합니다.** Neo4j나 PostgreSQL을 직접 고치거나, `sync_people()`를 부르지 않는 새 API를 만들면 그 순간부터 두 DB가 어긋납니다.
2. **PostgreSQL 반영이 실패하면:** Neo4j는 이미 저장된 상태입니다(두 DB를 한 트랜잭션으로 묶을 수 없음). Neo4j를 되돌리지 말고 오류를 기록한 뒤, 6-5의 방법으로 다시 맞춥니다.
3. **삭제 대신 비활성화.** 사람은 지우지 말고 `is_active: false`로 수정합니다. Neo4j의 할 일·추천·참석 기록이 user_id를 계속 가리킵니다.

### 6-5. 어긋났을 때 다시 맞추기

Neo4j 전체를 기준으로 PostgreSQL을 덮어씁니다. 같은 함수에 전체 목록을 넘기면 됩니다.

```python
from followup.adapters.neo4j import people      # backend
pg_people.sync_people(people.list_people())     # 활성·비활성 전원
```

Neo4j에서 지워진 사람이 PostgreSQL에 남아 있는 경우는 지우지 않습니다. 삭제 대신 비활성화를 쓰는 규칙(6-4)이라 생기지 않아야 합니다.

## 7. 아직 안 된 것

| 항목 | 상태 |
| --- | --- |
| 실제 DB 적재 | **미검증.** 파일 검사와 단위 테스트만 통과. 실제 PostgreSQL에서 INSERT·제약·외래 키는 아직 돌려보지 않음 |
| 비밀번호 로그인 | 안 됨. 로그인이 필요하면 해시 방식을 하나로 정해야 함 (backend: scrypt, playground: pbkdf2) |
| json에서 빠진 스킬 | 시드(`seed_people.py`)는 upsert만 해서 지우지 않음. 이후 `sync_people()`는 목록 전체를 교체해서 지움 |
| `sync_people()` 실제 동작 | **미검증.** 통합 테스트(`test_people_sync_add_update_deactivate`)는 만들었지만 DB가 없어 실행하지 못함 |
| 기존 표 외래 키 | `folders.host_user_id`, `folder_members.user_id`, `meetings.host_user_id` 등은 아직 TEXT. `people`/`accounts`에 연결할 수 있지만 기존 데이터 확인이 먼저 |
| 웹 API 이중 쓰기 | 반영 함수(`sync_people`)는 playground에 있음. backend API에는 아직 붙이지 않아서 지금은 Neo4j에만 씀 (6-3) |
| backend로 합치기 | `db.py`·`crud/people.py`를 backend로 옮기고, 시드는 backend `seed_people.py`의 `upsert_org()` 옆에, 이후 반영은 6-3처럼 API에 붙이는 방향 |
| 실패 기록 · 자동 재시도 | 없음. PostgreSQL 반영이 실패하면 지금은 API 오류로만 보임. 6-5를 손으로 실행해서 맞춤 |

## 8. 관련 파일

| 파일 | 역할 |
| --- | --- |
| [`seed_people.py`](seed_people.py) | users.json → PostgreSQL (처음 한 번) |
| [`crud/people.py`](crud/people.py) | 이후 추가·수정 반영 `sync_people()` (6장) |
| [`schema.sql`](schema.sql) | 새 DB용 전체 표 정의. **맨 앞에서 표를 모두 지움. 데이터 있는 DB에 실행 금지** |
| [`migrations/004_people.sql`](migrations/004_people.sql) | 기존 DB에 사람·조직·스킬 표 추가 |
| [`tests/test_seed_people.py`](tests/test_seed_people.py) | 시드 파싱 검사 (DB 없이) |
| [`tests/test_crud.py`](tests/test_crud.py) | `person_rows()` 변환·검사 (DB 없이) |
| [`tests/test_integration_pg.py`](tests/test_integration_pg.py) | `sync_people()` 추가→수정→비활성화 (테스트 DB 필요) |
| `backend/src/followup/entrypoints/seed_people.py` | 같은 파일 → Neo4j |
| `backend/src/followup/entrypoints/api/routes/people.py` | 사람 추가·수정 API. `sync_people()`를 붙일 곳 |
| `backend/seed/users.json`, `skills.json` | 원본 시드 파일 |
