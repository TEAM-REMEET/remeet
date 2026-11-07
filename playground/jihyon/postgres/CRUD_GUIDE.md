# RE:MEET PostgreSQL CRUD — 적용 가이드

기존 ZIP을 바탕으로 만든 **독립적인 Python DB 레이어**입니다. FastAPI / Streamlit 화면에는 아직 연결하지 않았습니다.

## 구성

| 파일 | 역할 |
|---|---|
| `crud/folders.py` | 폴더 생성·조회·수정·소프트 삭제, 멤버 추가·삭제 |
| `crud/meetings.py` | 회의 관리 정보 CRUD 및 원문 저장·조회 |
| `crud/minutes.py` | 회의 원본(버전) · 전체(월간/주간/일반) · 프로젝트별 회의 목록 (README 4-1) |
| `crud/detail.py` | 회의 상세: 요약(버전) · 참석자 · 액션 아이템 · 안건 분리 요청 (README 4-2) |
| `crud/shares.py` | 회의 공유 생성·조회·변경·철회 |
| `crud/notifications.py` | 알림 생성·조회·읽음 상태 변경·삭제 |
| `crud/accounts.py` | 계정 생성·조회·수정·삭제, 비밀번호 검증 |
| `crud/common.py` | 권한 확인, 공통 예외, 캐시 무효화 |
| `migration_001_accounts_role.sql` | 기존 DB의 `accounts.role`만 안전하게 추가 |
| `tests/test_crud.py` | DB 없이 실행하는 최소 검증 테스트 |

## 적용 순서 (중요)

1. **기존 PostgreSQL 백업**을 먼저 만듭니다. PowerShell에서 `docker exec remeet-postgres pg_dump -U remeet -d remeet -Fc > remeet_backup.dump`로 백업할 수 있습니다.
2. 이 폴더의 `crud/`와 `migration_001_accounts_role.sql`을 본인 `playground/jihyon/postgres/`에 복사합니다. 기존 `db.py`, `repository_pg.py`는 그대로 둡니다.
3. 이미 `role` 컬럼이 있다면 4번은 건너뛰어도 됩니다. 없다면 **초기화되는 `seed.py`를 재실행하지 말고** 아래 마이그레이션을 실행합니다.

   ```powershell
   Get-Content .\migration_001_accounts_role.sql -Raw | docker exec -i remeet-postgres psql -v ON_ERROR_STOP=1 -U remeet -d remeet
   ```

4. 데모 사용자와 관리자 데이터를 만들려는 **빈 개발 DB**라면 변경된 `seed.py`를 실행할 수 있습니다. **이 파일은 `schema.sql`을 실행해 기존 테이블을 삭제합니다.** 이미 사용 중인 DB에서는 실행하지 마세요.
5. `uv run python -m unittest discover -s tests -v`로 검증합니다. 테스트는 실제 DB를 변경하지 않습니다.
6. 테스트 통과 후 아래 샘플처럼 함수를 하나씩 호출합니다. 다른 사람 데이터에 접근하면 `PermissionDenied`가 발생합니다.

## 코드 사용 예

```python
import paths  # web/config.py 등을 참조하는 경우에는 먼저 import
from crud.folders import create_folder, list_folders, update_folder, delete_folder
from crud.meetings import create_meeting, update_meeting, replace_transcript
from crud.shares import create_share, revoke_share
from crud.notifications import create_notification, set_read
from crud.accounts import authenticate

# actor_id는 서버 인증 세션에서 검증된 ID여야 합니다. 사용자가 임의 입력한 값을 신뢰하면 안 됩니다.
folder = create_folder(actor_id='u1', name='주간 회의', type_id='weekly')
meeting = create_meeting(actor_id='u1', meeting_id='demo-new-001', folder_id=folder['id'])
update_meeting(actor_id='u1', meeting_id=meeting['id'], status='confirmed')
replace_transcript(actor_id='u1', meeting_id=meeting['id'], lines=[
    {'line_id':'1', 'speaker':'A', 'text':'이번 주 일정 공유할게요.'}
])
share = create_share(actor_id='u1', meeting_id=meeting['id'], recipient_user_id='u2')
revoke_share(actor_id='u1', share_id=share['id'])
notification = create_notification(actor_id='u1', user_id='u2', kind='shared', text='회의를 공유했습니다.', meeting_id=meeting['id'])
set_read(actor_id='u2', notification_id=notification['id'])
```

## 계정(로그인) 주의

- `seed.py`로 만들어진 데모 계정은 비밀번호 해시가 `NULL`이므로 로그인할 수 없습니다. 관리자가 아래처럼 비밀번호를 설정해야 합니다.

  ```python
  from crud.accounts import update_account
  update_account(actor_id='admin1', user_id='u1', password='replace-with-strong-password')
  ```

- `authenticate(email=..., password=...)`는 비밀번호를 검증하는 기능까지만 제공하며, **세션/JWT 발급, 로그인 횟수 제한, 비밀번호 초기화 및 UI 로그인은 구현되지 않았습니다.** 관리자 최초 비밀번호 등록(bootstrap)은 별도 운영 절차가 필요합니다. 실제 인증 서비스에 사용하려면 추가 보안 검토가 필요합니다.
- 생성/수정/삭제 권한은 `actor_id`로 검증하지만 현재 코드에서 이 값은 아직 세션에 연결되지 않았습니다. **클라이언트가 보내준 actor_id를 그대로 쓰면 보안상 취약합니다.**
- 계정 삭제는 현재 물리 삭제이며 관련 Neo4j 인물 데이터/기존 작성 자료는 삭제하지 않습니다. 실서비스에서는 비활성화 필드를 추가하는 것을 권장합니다.

## Neo4j 및 UI 연동 시 주의

- `create_meeting`은 PostgreSQL 메타데이터만 만듭니다. 제목·날짜·참석자·안건 등은 Neo4j에서 별도로 만들어야 하며, 두 DB에 동일한 `meeting_id`를 전달해야 합니다.
- 기존 `repository_pg._base_meetings()`는 Neo4j/mock에 없는 회의는 표시하지 않습니다. DB INSERT 성공 후에도 화면에 안 보일 수 있습니다.
- `recipient_team_id` 공유는 데이터만 저장합니다. 팀원 목록 검증 및 팀 공유된 회의 접근 허용은 Neo4j 조직 쿼리를 연결할 때 구현해야 합니다. 현재는 안전하게 허용하지 않습니다.
- **원문 정책 확정 필요:** 현 구현에서는 폴더 멤버가 원문을 볼 수 있습니다. 팀 정책상 더 제한해야 하면 `get_transcript`의 권한 조건을 강화하세요.
- 공유된 회의의 `summary`만 볼 수 있어야 하는 사용자에게도 기존 `repository_pg` 어댑터의 `Paragraph` 데이터가 그대로 조립될 수 있습니다. **이 CRUD 모듈의 원문 권한 검사만으로 기존 화면이 안전해지는 것은 아닙니다.** UI 조회 경로에서도 `scope/lock_raw` 필터링이 필요합니다.
- 캐시 무효화는 같은 프로세스에 적재된 `repository_pg`만 처리합니다. 다른 프로세스 간 캐시는 별도 전략이 필요합니다.
- 본 코드에는 DB를 이용한 실제 통합 테스트가 아직 없습니다. 개발용 임시 DB를 따로 만들고 Create→Read→Update→Delete와 권한 거부, rollback 테스트를 추가한 뒤 사용하세요.

## 현재 지원 범위 밖

`meeting_types`, `folder_briefs`, `meeting_speakers`, `meeting_analyses`, `meeting_keywords`, `integration_*`, `failed_exports`, `app_settings` 전체 CRUD는 **아직 작성하지 않았습니다.** 이번 구현은 요청한 **폴더 → 회의 → 공유 → 알림 → 로그인 순서의 핵심 동작**에 집중했습니다.
