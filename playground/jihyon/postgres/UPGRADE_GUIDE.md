# RE:MEET PostgreSQL 보강 패치 (2026-11-06)

## 범위

- 독립 `remeet_test` 데이터베이스에서 CRUD 통합 테스트 (기존 9개 + 알림/RLS/제약조건 3개)
- 폴더 멤버 추가·개인 회의 공유 시 **DB 트리거**로 알림 생성: 해당 INSERT 실패 시 알림도 롤백
- RLS: `remeet_app` **NOLOGIN 권한 역할**과 트랜잭션 사용자 ID(`remeet.user_id`)를 이용해 폴더/회의/원문/개인 공유/알림 제한
- 제약조건: 이메일 형식, 자기 자신 공유, 중복 활성 공유, 회의 상태 전환
- 오류 메시지 변환 헬퍼 `crud/db_errors.py`
- `migrate.py` 번호 기반 SQL 적용·기록·체크섬 검증 / `seed.py --reset`만 허용

## 적용 주의

**기존 `remeet` DB에 바로 실행하지 마세요.** 스키마·권한 변경 사항은 사용자 DB에 적용하기 전에 전용 테스트 DB에서 먼저 검증해야 합니다.

1. 기존 `playground/jihyon/postgres`를 백업하고 새 파일을 복사합니다. 기존 `.env`는 덮어쓰지 마세요.
2. PowerShell에서 테스트 DB 생성 (이미 있으면 생략):

   ```powershell
   docker exec remeet-postgres psql -U remeet -d postgres -c "CREATE DATABASE remeet_test;"
   ```

3. **테스트 DB** 명시:

   ```powershell
   cd C:\remeet\playground\jihyon\postgres
   $env:TEST_DATABASE_URL="postgresql://remeet:remeet@127.0.0.1:5432/remeet_test"
   $env:DATABASE_URL=$env:TEST_DATABASE_URL
   uv run python -m unittest discover -s tests -p test_integration_pg.py -v
   ```

   이 테스트는 이름이 `_test`로 끝나는 DB에서만 수행됩니다. 테스트 DB의 테이블은 초기화됩니다.

4. 기존 단위 테스트:

   ```powershell
   uv run python -m unittest discover -s tests -p test_crud.py -v
   ```

5. 테스트 통과 후, 기존 운영/개발 DB를 **백업한 뒤** 별도로 스키마를 검토하고 `python migrate.py` 실행. `DATABASE_URL`은 명시해야 합니다. 변경 내역은 `schema_migrations`로 추적합니다. 이미 존재하는 중복 공유 데이터가 있으면 유니크 인덱스에서 실패하며 **전체 마이그레이션이 롤백**됩니다.

## RLS 적용 조건 및 한계

- `migrations/002_rls.sql`은 `remeet_app`을 **NOLOGIN**으로 생성합니다. 로그인 가능한 제한 계정과 DB 비밀번호는 배포 담당자가 별도로 관리하며 해당 역할을 부여해야 합니다. 기존 테이블 소유자 `remeet`로 접속하면 RLS가 우회되므로 RLS가 적용됐다고 볼 수 없습니다.
- 제한 계정 접속 시 모든 DB 작업은 신뢰할 수 있는 **서버에서 확정한 `actor_id`**로 `transaction(actor_id=...)`를 호출해야 합니다. HTTP 요청자가 보낸 `actor_id`를 그대로 전달하면 안 됩니다. `repository_pg.py`의 기존 조회는 별도 전환 작업이 필요합니다.
- RLS의 `remeet.user_id`는 일반 custom GUC이므로, 임의 SQL을 실행할 수 있는 상대가 있다면 사용자 정보를 위조할 수 있습니다. SQL 인젝션 방지, 최소 권한, 서버 측 인증이 전제입니다. 'RLS가 있으니 별도 접근 검사가 불필요하다'고 생각하면 안 됩니다.
- 제한 역할에 대해 승인한 테이블 일부만 GRANT했습니다. `meeting_speakers`, `meeting_keywords`, `meeting_analyses`, 기타 관리 테이블은 아직 제한 역할 접근이 없어 추가 설계가 필요합니다. 특히 **관리자 비용/구성원/연동 관리 쪽 별도 역할/API**는 현재 포함하지 않았습니다.
- 팀 공유, Neo4j 구성원 확인, 담당자 지정/안건 분리 알림, 새 회의 참여자 알림, 실제 프론트 화면 복구는 연동 후 구현해야 합니다.
- 자동 알림은 기존 Python `create_notification`을 호출하지 않고 DB 트리거로 생성됩니다. 수동 알림 생성은 별도 흐름이며 제한 역할의 직접 알림 INSERT는 허용되지 않습니다.

## 오류 처리와 재시도

- `crud/db_errors.user_message()`는 오류를 사용자용 한국어로 변환하는 함수입니다. **아직 Streamlit/FastAPI 최상위 오류 처리기에 연결하지 않았습니다.** 작업 단위 중복 실행 문제가 있어 쓰기 작업을 무조건 자동 재시도하지 않습니다. 직렬화 실패/교착상태(40001/40P01)는 idempotency 정책과 함께 별도 구현이 필요합니다.
- `db.py`의 5초 풀 대기시간은 기존처럼 유지했습니다. 사용자별 `actor_id`는 `set_config(..., true)`로 트랜잭션 범위에서만 설정합니다.

## 실행 검증 범위

- 코드 Python 컴파일 및 기존 DB 없는 기본 단위 테스트는 검사했습니다.
- 이 환경에서는 PostgreSQL/Docker 서버가 실행되지 않아 **실 DB 통합 테스트는 미실행**입니다. 테스트 결과를 확인한 후 릴리스 여부를 결정하세요.
