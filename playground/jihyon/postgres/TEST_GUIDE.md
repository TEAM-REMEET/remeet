# PostgreSQL CRUD 통합 테스트 안내

이 테스트는 **기존 `remeet` 데이터베이스를 전혀 변경하지 않고**, 이름이 `_test`로 끝나는 별도의 DB만 사용합니다.
**주의:** 실행 시 지정된 `*_test` DB 내부의 테이블은 `schema.sql`로 초기화됩니다. 반드시 새 빈 테스트 DB만 사용하세요.

## 1. 테스트 DB 생성 (PowerShell)

```powershell
docker exec remeet-postgres psql -U remeet -d postgres -c "CREATE DATABASE remeet_test;"
```
이미 존재한다는 오류가 나오면 새 DB를 이미 만든 것이므로 다음 단계로 가세요. 테스트 DB를 기존에 사용하고 있었다면 절대 바로 실행하지 말고 내부 데이터가 삭제돼도 되는지 먼저 확인하세요.

## 2. 테스트 코드 배치

다운로드한 ZIP의 `tests/test_integration_pg.py`를 기존 `C:\remeet\playground\jihyon\postgres\tests` 아래에 복사하세요. **`schema.sql`은 현재 프로젝트에 있는 것을 그대로 사용합니다.**

## 3. 테스트 전용 DB 설정 후 실행

```powershell
cd C:\remeet\playground\jihyon\postgres
$env:TEST_DATABASE_URL="postgresql://remeet:remeet@127.0.0.1:5432/remeet_test"
uv run python -m unittest discover -s tests -p test_integration_pg.py -v
```

테스트는 9개입니다. 성공 시 `Ran 9 tests ... OK`, 실패 시 `FAIL` / `ERROR`와 함수명이 표시됩니다.

## 4. 중요

- 기존 코드에서 관리자 접근을 허용하는 `is_admin` 조건이 남아 있다면 `test_admin_denied_all_meeting_and_folder_access`가 실패해야 정상입니다. 이 실패는 테스트 문제라기보다 **수정해야 할 접근 권한 문제를 발견했다는 뜻**입니다.
- `test_summary_share_overrides_folder_membership_for_raw`가 실패하면 폴더 멤버가 요약 공유를 우회해 원문을 볼 수 있다는 뜻입니다.
- Neo4j를 통한 로그인은 별도 담당이므로 이 테스트에서는 `accounts`에 **권한 확인용 임시 사용자 ID만** 넣습니다. 실제 계정 인증 구현을 검증하지 않습니다.
- `TEST_DATABASE_URL`을 지정하지 않으면 이 통합 테스트는 실행되지 않습니다(skipped).
- 테스트는 실제 PostgreSQL 서버가 있어야 통과 여부를 확인할 수 있습니다.
- 현재 코드에서 추가로 수정한 함수 이름이나 매개변수가 다르다면 테스트 파일도 그에 맞춰 조정해야 합니다.
