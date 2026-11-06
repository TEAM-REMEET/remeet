# 구성원 관리 · 로그인 (Neo4j 연동)

구성원(사람) 데이터와 로그인을 Neo4j에 연결한 부분의 안내서다.
처음 보는 사람은 1 → 2 → 3 순서로 읽고, 필요할 때 4~7을 찾아본다.

## 1. 무엇을 할 수 있나

| 기능 | 누가 | 화면 |
|---|---|---|
| 이메일 · 비밀번호로 로그인 (7일 유지) | 모든 구성원 · 관리자 | 로그인 화면 |
| 구성원 목록 보기 (팀 · 직무 · 직급 · 권한 · 상태) | 관리자 | 관리자 › 구성원 관리 |
| 구성원 추가 (이름 · 이메일 · 팀 · 직무 · 직급 · 권한 · 스킬과 숙련도) | 관리자 | 구성원 관리 › 구성원 추가 |
| 구성원 수정 (팀 · 직무 · 직급 · 권한 · 활성 · 스킬과 숙련도) | 관리자 | 구성원 관리 › 연필 버튼 → 수정 팝업 |
| 회의 참석자 · 담당자 고르기에 Neo4j 구성원 사용 | 모든 구성원 | 새 회의록 등 |

웹에서 추가 · 수정하면 바로 Neo4j에 저장된다. 웹은 Neo4j에 직접 접속하지 않고 항상 백엔드 API를 거친다.

## 2. 전체 흐름

```
브라우저 ─▶ Nginx ─┬─ /      ─▶ Streamlit 웹 (apps/web) ──┐ 서버 안에서 직접 호출
                   │                                       ▼ http://backend:8000
                   └─ /api/  ─▶ FastAPI 백엔드 (backend) ──▶ Neo4j Aura
```

- **로그인**: 웹이 `POST /api/auth/login`으로 이메일 · 비밀번호를 보낸다. 백엔드가 Neo4j의 비밀번호 해시와 비교하고, 맞으면 **출입증(토큰)** 을 준다.
- **이후 요청**: 웹이 출입증을 `Authorization: Bearer <토큰>` 헤더에 붙여 보낸다. 백엔드는 출입증으로 누구인지, 관리자인지 확인한다.
- **새로고침**: 출입증은 브라우저 쿠키(`remeet_token`, 7일)에도 저장된다. 새로고침하면 `GET /api/auth/me`로 다시 확인해서 로그인을 되살린다.
- **만료**: 7일이 지나거나 서명 키(`AUTH_SECRET`)가 바뀌면 출입증이 무효가 되고 로그인 화면으로 돌아간다. 비밀번호는 그대로다.

## 3. 파일 안내

### 백엔드 (`backend/`)

| 파일 | 하는 일 |
|---|---|
| `src/followup/settings.py` | 환경변수를 읽는 유일한 곳. 필수값(`NEO4J_PASSWORD`, `AUTH_SECRET`)이 비면 서버가 시작하지 않는다 |
| `src/followup/domain/passwords.py` | 비밀번호 → 해시 만들기 · 맞는지 검사 (표준 라이브러리 scrypt) |
| `src/followup/domain/tokens.py` | 출입증 만들기 · 검사 (HMAC 서명, 만료 시각 포함) |
| `src/followup/adapters/neo4j/driver.py` | Neo4j 연결 하나를 만들어 재사용, 유일성 제약 적용 |
| `src/followup/adapters/neo4j/schema.cypher` | 유일성 제약 (user_id · team_id 등) |
| `src/followup/adapters/neo4j/people.py` | 구성원 · 팀 · 직무 · 스킬 · 관리 계정 적재와 조회 · 추가 · 수정 Cypher |
| `src/followup/entrypoints/api/routes/auth.py` | 로그인 API, 로그인 확인(`current_user`) · 관리자 확인(`require_admin`) |
| `src/followup/entrypoints/api/routes/people.py` | 구성원 · 팀 · 직무 · 스킬 API |
| `src/followup/entrypoints/api/main.py` | FastAPI 앱. 라우터 등록, 시작할 때 필수 설정 확인 |
| `src/followup/entrypoints/seed_people.py` | 처음 한 번 구성원 적재 (아래 6절) |
| `seed/users.json` · `seed/skills.json` | 적재 원본 (팀 · 직무 · 사람 · 관리 계정 · 스킬). 비밀번호는 해시만 들어 있다 |
| `seed/passwords.local.json` | **비밀번호 원문. git에 올라가지 않는다** (팀원에게 따로 전달) |
| `tests/unit/test_people.py` | 단위 테스트 23개 (DB 없이 실행) |

### 웹 (`apps/web/`)

| 파일 | 하는 일 |
|---|---|
| `config.py` | `API_BASE_URL` (백엔드 주소), 로그인 쿠키 이름, 관리자 문의 이메일 |
| `data/api.py` | 백엔드 호출 도우미. 실패하면 `ApiError(메시지, 상태 코드)` |
| `data/repository.py` | 화면이 쓰는 데이터 함수. 구성원 · 팀 · 직무는 API에서 (60초 캐시), 나머지는 아직 목업 |
| `state/store.py` | 세션 상태. `user_id` · `token` 등 로그인한 사람, `me_id()` |
| `state/actions.py` | 로그인(`login` · `start_session`), 구성원 추가(`adm_add_member`) · 수정(`open_member_edit` · `save_member_edit`) |
| `state/selectors.py` | 구성원 관리 목록 행 만들기. 없는 사람 id는 "알 수 없음"으로 |
| `views/login.py` | 로그인 화면 |
| `views/admin/members.py` | 구성원 관리 화면 (목록 · 추가 줄) |
| `components/dialogs/admin.py` | 구성원 수정 팝업 (`member_edit_dialog`) |
| `components/member_fields.py` | 직급 · 스킬과 숙련도 입력칸 (추가 줄 · 수정 팝업 공용) |
| `app.py` | 쿠키로 로그인 되살리기, 로그인 만료 · 백엔드 오류 처리 |

### 배포 (`deploy/`)

| 파일 | 하는 일 |
|---|---|
| `docker-compose.yml` | 웹 · 백엔드 · Nginx. 웹에 `API_BASE_URL=http://backend:8000` |
| `.env.example` | 서버의 `deploy/.env` 견본 (Neo4j, `AUTH_SECRET`, OpenAI) |

## 4. API 명세

`/api/docs`(로컬: http://localhost:8000/api/docs)에서 직접 눌러 볼 수 있다. 오른쪽 위 **Authorize**에 로그인 응답의 `token`을 넣으면 로그인이 필요한 API도 시험할 수 있다.

| 메서드 · 주소 | 필요한 권한 | 요청 | 응답 |
|---|---|---|---|
| `POST /api/auth/login` | 없음 | `{email, password}` | `{token, user: {user_id, name, email, permission}}` |
| `GET /api/auth/me` | 로그인 | — | `{user_id, name, email, permission}` |
| `GET /api/people` | 로그인 | — | 구성원 목록 (아래 형식) |
| `GET /api/people/{user_id}` | 로그인 | — | 구성원 한 명 |
| `POST /api/people` | 관리자 | `{name, email, team_id, job, position, permission?, skills?}` | 만든 구성원 (201) |
| `PATCH /api/people/{user_id}` | 관리자 | 바꿀 항목만 (`skills`는 목록 전체 교체) | 바뀐 구성원 |
| `GET /api/teams` · `/api/roles` · `/api/skills` | 로그인 | — | 선택 목록 |
| `GET /api/health` | 없음 | — | `{status: "ok"}` (Docker 헬스체크) |

구성원 형식:
```json
{"user_id": "u1", "name": "바트", "email": "bart@remeet.example", "permission": "member",
 "position": "senior", "is_active": true, "team_id": "t-pm", "team": "프로젝트 관리팀",
 "role_id": "r-pm", "job": "프로젝트 매니저",
 "skills": [{"skill_id": "PM-01", "name": "일정·마일스톤 계획", "level": 4}]}
```

입력 규칙: `position`은 `senior | mid | junior`, `permission`은 `admin | member`, 스킬 `level`은 1~4, 이메일은 소문자로 저장된다.
목록에 없는 `job`을 보내면 직무(Role)가 새로 만들어진다.

| 상태 코드 | 뜻 |
|---|---|
| 401 | 로그인 안 함 · 출입증 만료 · 이메일이나 비밀번호 틀림 |
| 403 | 관리자 아님 · 비활성 계정 |
| 404 | 없는 구성원 |
| 409 | 이미 쓰는 이메일 |
| 400 | 없는 팀 · 스킬 |
| 422 | 형식 오류 (이메일 모양, 직급 값, 숙련도 범위 등) |

## 5. Neo4j 데이터

```
(Person)-[:MEMBER_OF]->(Team)
(Person)-[:HAS_ROLE]->(Role)
(Person)-[:HAS_SKILL {level, updated_at}]->(Skill)
(Admin)                                  관리 전용 계정. 구성원 목록 · 담당자 추천 후보에 나오지 않음
```

| 노드 | 필드 |
|---|---|
| `Person` | `user_id`, `name`, `email`, `password_hash`, `permission`(admin/member), `is_active`, `position`(junior/mid/senior) |
| `Admin` | `user_id`(admin), `name`, `email`, `password_hash` |
| `Team` | `team_id`, `name`, `is_active` |
| `Role` | `role_id`, `name` |
| `Skill` | `skill_id`, `name`, `description`, `domain`, `tools`, `source`, `ncs_code`, `ncs_name` |

숙련도 기준: 1 지시받아 보조 수행 가능 · 2 단독으로 수행 가능 · 3 결과물의 품질을 책임지고 판단 가능 · 4 타인을 지도하거나 기준을 수립 가능.

비밀번호 원문은 어디에도 저장하지 않는다. `password_hash`는 API 응답에 절대 나가지 않는다 (테스트로 확인).

## 6. 로컬에서 실행 · 확인

`remeet/.env`에 `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `AUTH_SECRET`이 있어야 한다.

```bash
# 백엔드 (backend 폴더)
PYTHONPATH=src uv run --project .. uvicorn followup.entrypoints.api.main:app --port 8000 --reload
# 웹 (apps/web 폴더, 백엔드가 켜져 있어야 함)
uv run --project ../.. streamlit run app.py
# 테스트 (backend 폴더)
uv run --project .. python -m unittest tests.unit.test_people -v
```

- 웹: http://localhost:8501 → `passwords.local.json`의 이메일 · 비밀번호로 로그인. 관리자는 `admin@remeet.example`.
  - 로그인 화면의 「데모 계정으로 바로 시작」(바트 u1) · 「관리자 계정으로 시작」(admin) 버튼으로 바로 들어갈 수 있다. 이 버튼은 `passwords.local.json`이 있을 때만 보이고, Docker 이미지에는 이 파일이 없어서 서버에서는 자동으로 숨겨진다.
- API 문서: http://localhost:8000/api/docs

구성원 적재 (`backend` 폴더, Neo4j가 비어 있을 때 처음 한 번):

```bash
PYTHONPATH=src uv run --project .. python -m followup.entrypoints.seed_people --check     # 파일만 검사, DB에 안 씀
PYTHONPATH=src uv run --project .. python -m followup.entrypoints.seed_people             # 빈 DB에 적재 (구성원이 있으면 거부)
PYTHONPATH=src uv run --project .. python -m followup.entrypoints.seed_people --accounts  # 이미 있는 DB에 계정만 채움
```

`--force`는 이미 있는 구성원을 파일 값으로 덮어써서 **웹에서 고친 내용이 사라진다**. DB를 처음부터 다시 만들 때만 쓴다.
적재 스크립트는 Docker 이미지에 들어 있지 않다. 로컬에서 Aura에 직접 실행한다.

## 7. 클라우드(EC2) 배포 체크리스트

Docker 이미지 빌드, 컨테이너 실행, Nginx를 거친 웹 · API · 로그인을 로컬 Docker Compose로 확인했다 (2026-11-06).

1. **`deploy/.env` 만들기**: `.env.example`을 복사해 값을 채운다.
   - `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `AUTH_SECRET`은 필수다. 하나라도 비면 백엔드가 시작하지 않고 로그에 이유가 남는다 (`docker compose logs backend`).
   - **`이름=값` 사이에 띄어쓰기를 넣지 않는다.** `OPENAI_API_KEY =...`처럼 쓰면 Docker가 파일을 읽지 못한다. (`remeet/.env`의 첫 줄이 이렇게 되어 있으니 복사할 때 고친다.)
   - `AUTH_SECRET` 만들기: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. 바꾸면 모두 다시 로그인해야 한다.
2. **HTTPS 붙이기 (외부 공개 전 필수)**: 지금 Nginx는 HTTP(80)만 연다. 그대로 공개하면 로그인 비밀번호가 암호화되지 않은 채 인터넷을 지나간다. 도메인을 연결하고 인증서(Let's Encrypt 등)를 Nginx에 붙이거나 AWS 로드밸런서에서 HTTPS를 처리한다.
3. **EC2 보안 그룹**: 들어오는 포트는 80(과 443)만 연다. 8000 · 8501은 열지 않는다 (컨테이너끼리만 통신).
4. **Neo4j Aura**: EC2에서 밖으로 나가는 연결(7687)은 기본으로 허용되어 있어 따로 할 일이 없다.

## 8. 알려진 한계

- **웹에서 추가한 구성원은 비밀번호가 없어서 아직 로그인할 수 없다** → 관리자가 처음 비밀번호를 정하는 기능이 필요하다.
- 출입증은 로그인할 때의 권한을 담고 만료(7일)까지 다시 확인하지 않는다. 비활성화하거나 권한을 바꿔도 그 사람이 다시 로그인할 때 반영된다.
- 로그인 시도 횟수 제한이 없다 (비밀번호 대입 공격 대비 필요).
- 로그인 쿠키는 브라우저 스크립트로 만들어서 HttpOnly가 아니다. 실제 서비스 전에 바꾸는 것이 안전하다.
- 다른 곳(Neo4j 콘솔 등)에서 바꾼 구성원 정보는 웹에 최대 60초 늦게 보인다 (캐시).
- 회의 · 폴더 · 알림은 아직 목업이다. 목업이 가리키는 옛 사람 id는 "알 수 없음"으로 보인다.
- 팀은 계층(본부 › 부)이 없다. 구성원 삭제 기능은 없다 (비활성화만).
