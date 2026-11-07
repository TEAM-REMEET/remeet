# PostgreSQL 어댑터

회의 목록 · 권한 · 상태 · 공유 · 알림 · 검색 · 외부 연동 기록을 PostgreSQL 에 저장하고 읽어요.
Neo4j 어댑터(`adapters/neo4j`)와 같은 방식이에요: 연결은 `db.py` 하나, 주제별 함수 파일, 실행 스크립트는 `entrypoints/`.

## 1. 두 DB 나누는 규칙

| 데이터 | 원본 | 다른 쪽 |
| --- | --- | --- |
| 사람 · 팀 · 직무 · 스킬, **로그인** | Neo4j | PostgreSQL 에 복사 (`people.sync_people`, `POST/PATCH /api/people` 이 Neo4j 저장 뒤 호출) |
| 회의 정보(제목 · 날짜 · 프로젝트) · **참석자** · 상태 · 권한 · 공유 · 알림 | PostgreSQL | 확정 시 Neo4j 에 복사 (`adapters/neo4j/load.py` 의 `meeting["attendees"]` 에 PostgreSQL 값을 넘김) |
| 안건 · 주제 · 액션 아이템 그래프 · 담당자 추천 | Neo4j | — |

- 두 DB 는 **같은 ID** 로 이어져요: `meetings.id = Meeting.meeting_id`, `folders.id = Project.project_id`, `*_user_id = Person.user_id`.
- PostgreSQL `accounts` 는 비밀번호 없이 사람 ID 명부 역할만 해요. 회의 · 폴더 · 공유 · 알림의 사람 칸이 여기에 외래 키로 걸려 있어서(migrations/012), **사람은 꼭 `/api/people` 로 추가**해야 회의에 넣을 수 있어요.
- 스킬 레벨은 1~4 (backend `SkillLevel` 과 같음, migrations/014).

## 2. 파일

| 파일 | 하는 일 |
| --- | --- |
| `db.py` | 커넥션 풀 · `transaction(actor_id)` · `close_pool()`. 주소는 `settings.DATABASE_URL` |
| `schema.sql`, `migrations/`, `migrate.py` | 표 정의. 바꿀 때는 **새 번호 파일**을 추가 (적용된 파일을 고치면 migrate 가 멈춤) |
| `common.py` | 예외(`NotFoundError` · `PermissionDenied` · `ValidationError`) · 권한 검사 |
| `folders.py` | 프로젝트(폴더) · 멤버 · 브리프 |
| `minutes.py` | 사이드바 트리, 회의 목록 (정기 · 프로젝트 · 전체, 최신순), 원본 파일 |
| `meetings.py` | 회의 기본 정보 · 원문 · 상태 이력 |
| `compose.py`, `detail.py` | 회의록 작성 흐름 · 상세 · 요약 · 할 일 (**회의록 담당 팀원이 API 로 연결**) |
| `search.py` | 검색 (pg_trgm) |
| `shares.py`, `notifications.py`, `integrations.py`, `ask.py` | 공유 · 알림 · 외부 연동 · 물어보기 기록 |
| `people.py`, `seed_people.py` | 구성원 반영 · 시드 |

모든 함수는 `actor_id` 를 받아서 권한을 검사해요. **`actor_id` 는 로그인 토큰의 `user_id` 만** 넘겨요
(`entrypoints/api/routes/actor.py`). 예외 → HTTP 상태는 `entrypoints/api/errors.py` 가 한곳에서 바꿔요
(404 · 403 · 400 · 제약 위반 409 · DB 접속 실패 503).

## 3. 처음 실행 (backend 폴더에서)

```powershell
docker compose -f docker-compose.postgres.yml up -d          # 개발용 DB 켜기 (healthy 확인: ... ps)
$env:PYTHONPATH="src"
python -m followup.entrypoints.seed_postgres migrate          # 표 만들기 · 업데이트
python -m followup.entrypoints.seed_postgres people           # 구성원 (backend/seed/users.json)
uvicorn followup.entrypoints.api.main:app --reload            # API → http://localhost:8000/api/docs
```

- 주소는 `remeet/.env` 의 `DATABASE_URL` (없으면 `postgresql://remeet:remeet@localhost:5432/remeet`).
- 개발 DB 를 처음 상태로: `seed_postgres reset` → `seed_postgres people --force`.
- Neo4j 와 구성원이 어긋났을 때: `seed_postgres sync-from-neo4j`.
- 배포: `deploy/docker-compose.yml` 의 `postgres` 컨테이너 (`deploy/.env` 에 `POSTGRES_PASSWORD`).
  표 만들기는 `docker compose exec backend python -m followup.entrypoints.seed_postgres migrate`.

## 4. API (routes/projects · shares · notifications · search · integrations)

| 메서드 | 경로 | 내용 |
| --- | --- | --- |
| GET | `/api/folders/tree` | 사이드바: 정기 회의 트리 + 프로젝트 트리 |
| GET | `/api/meetings` | 내가 볼 수 있는 전체 회의, 최신순 (`limit` · `offset`) |
| GET | `/api/meeting-types/{type_id}/meetings` | 정기 회의(weekly · monthly · normal) 안 회의 |
| GET | `/api/meetings/{id}/status-history` | 상태 변경 이력 |
| GET · POST | `/api/projects` | 내 프로젝트 · 만들기 |
| GET · PATCH · DELETE | `/api/projects/{id}` | 하나 · 이름 바꾸기 · 지우기 (주관자) |
| POST · DELETE | `/api/projects/{id}/members[/{user_id}]` | 멤버 추가 · 빼기 (주관자) |
| GET | `/api/projects/{id}/meetings` | 프로젝트 안 회의, 최신순 |
| GET · PUT | `/api/projects/{id}/briefs` | 폴더 브리프 (수정은 주관자) |
| POST · GET | `/api/meetings/{id}/shares` | 공유하기 · 공유 목록 |
| PATCH · DELETE | `/api/shares/{share_id}` | 범위 바꾸기 · 공유 취소 |
| GET | `/api/shares/sent`, `/api/shares/received` | 공유한 회의 · 공유 받은 회의 |
| GET | `/api/notifications` | 내 알림 (`unread_only`) |
| PATCH · DELETE | `/api/notifications/{id}` | 읽음 표시 · 지우기 |
| GET | `/api/search?q=` | 회의명 · 폴더명 · 구성원 · 내용 탭 결과 |
| GET | `/api/integrations/tools` | 연동 도구 · 상태 · 마지막 동기화 |
| PUT | `/api/integrations/tools/{id}/status` | 연결 상태 (관리자) |
| GET | `/api/integrations/failures` | 전송 실패 (관리자 전체 · 사용자는 `meeting_id` 로) |
| POST | `/api/integrations/failures/{id}/resolve` | 다시 보내 해결 (simple: 주관자 · tech: 관리자) |

관리자 계정은 회의 · 폴더 기능을 쓸 수 없어요 (403). 회의록 작성 · 상세 · 요약 · 할 일 API 는 회의록 담당 팀원이
`compose.py` · `detail.py` 로 연결해요.

## 5. 테스트 (backend 폴더에서)

```powershell
python -m unittest discover -s tests/unit -p "test_postgres_*.py" -v      # DB 없이
$env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
python -m unittest discover -s tests/integration/postgres -v              # 실제 DB (이름이 _test 인 DB 만, 표 초기화됨)
```

테스트 DB 만들기 (한 번만): `docker exec remeet-postgres psql -U remeet -d postgres -c "CREATE DATABASE remeet_test;"`