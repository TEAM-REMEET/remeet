# 요약 · 액션 아이템 추출 — 명세

## 입력

`회의 ID`와 전사문 텍스트. `POST /api/meetings` 입력은 `analysis/summary/transcript_builder.py`가 이 형식으로 바꾼다 (아래 '프론트 입력 변환').

```text
회의 제목: …
참여자: 이름_직무_userid, … (N명)
발화자: 이름_userid, …          (화자를 매핑한 경우에만)
일시: YYYY-MM-DD

이름_userid: 발화문
```

## 출력

`{회의 ID}_summary.json` 하나. 스키마는 `backend/src/followup/analysis/summary/schemas.py`의 `MeetingSummary`이다.

- `meeting_info`, `transcript`: 회의 정보와 발화 (사람은 `user_id`로 식별)
- `agendas`: 안건 → 논의 배경 · 주요 논의 · 미해결 이슈 · 다음 회의 후보
- `agenda_index`, `meeting_results`: 항목을 `item_id`로 참조하는 목차와 결과 (결정 · 액션 · 미해결 · 다음 회의)
- `verification`: 회차별 검증 문제와 재수정 내역, 최종 상태 (`PASSED`, `REVIEW_REQUIRED`)

## 처리 흐름

입력 검사 → 생성(LLM) → 후처리 → 검증(LLM) ⇄ 재수정(LLM) → 마무리 → 저장

## 요구사항

테스트는 `backend/tests/unit/test_summarize_meeting.py`의 `test_sa{번호}_*`이다.

| ID | 요구사항 |
|---|---|
| SA-001 | 정상 실행하면 `summary.json`만 저장하고, 호출한 쪽에 `MeetingSummary`를 반환한다. |
| SA-002 | 액션은 주요 논의의 `[액션][결정]` 항목이며, 담당자는 여러 명일 수 있고, `assignees` · `assignee_user_ids` · `assignee_names` · `assignee_jobs` 배열에 같은 순서로 저장한다. 담당자 · 기한은 없어도 된다. |
| SA-003 | 다른 직무의 동명이인에게 붙인 A · B는 LLM 입력에만 쓰고, 저장 전에 표시 이름으로 되돌린다. |
| SA-004 | 생성 후 반드시 1회 이상 검증하고, 처리할 문제가 있으면 항목 단위로 재수정한 뒤 다시 검증한다. 개선 유형(`settings.IMPROVEMENT_ISSUE_TYPES`)은 1회차에만 재수정 대상이고, 이후에는 기록만 남긴다. |
| SA-005 | 검증은 최대 3회이며, 마지막까지 남은 오류 유형 문제의 항목과 회의 상태는 `REVIEW_REQUIRED`로 저장한다. |
| SA-006 | 생성 결과가 비면 1회 재호출하고, 그래도 비면 저장 없이 실패한다. 재수정으로 항목이 모두 삭제되면 `REVIEW_REQUIRED`이다. |
| SA-007 | 입력 · 회의 ID · 설정 오류는 LLM을 호출하기 전에 중단한다. |
| SA-008 | `due`는 `YYYY-MM-DD` 또는 `YYYY-MM-DD HH:MM`만 저장한다. 그 외 값은 `null`로 바꾸고 이력에 남긴다. |
| SA-009 | 저장 결과에 API 키 · 사용량 · 비용을 넣지 않고, 프롬프트 버전을 기록한다. |
| SA-010 | 중간에 실패하면 결과를 저장하지 않으며, 같은 입력으로 재실행하면 실패한 단계부터 호출한다. 입력 · 프롬프트 · 모델이 바뀌면 다시 호출한다. 저장에 성공하면 단계별 응답을 삭제한다. |
| SA-011 | 실행 1회당 API 호출 상한을 지키고, 동시에 실행한 회의끼리 호출 수 · 결과가 섞이지 않는다. |

## 보류

- **API · Worker · ADMIN/VIEWER 권한:** 호출 경로가 없어 아직 적용하지 않는다. API를 추가할 때 이 명세에 조건을 추가한다.
- **컨테이너 교체 후 복원:** `backend_outputs` 볼륨에 의존한다. 외부 저장소를 정한 뒤 다시 정의한다.
- **결과 덮어쓰기:** 재실행하면 `summary.json`을 새 결과로 덮어쓴다. 검토 · 확정 단계(`specs/005`)에서 확정본 보호를 정의한다.
- **외부 참석자:** 참여자 목록에 없는 화자는 입력 오류로 처리한다.

## 프론트 입력 변환

`POST /api/meetings`는 `project_id` · 제목 · 날짜 · 주관자(`host_user_id`) · 참석자(`user_id`, `name`, `job`) · 입력 형식 · 전사문 · 발화자 연결(표기 → `user_id`)을 받아 `MeetingSummary`를 돌려준다. 요약 코드는 Neo4j에 접근하지 않는다.

| ID | 동작 |
|---|---|
| IN-001 | `plain`, `bracket`, `colon` 형식을 읽어 `이름_userid: 발화문`으로 통일한다. `plain`은 발화문을 그대로 두고 `발화자` 줄을 넣지 않는다. |
| IN-002 | 사람과 연결되지 않은 발화자 표기가 있으면 LLM을 호출하기 전에 중단한다 (미지정 허용 안 함). |
| IN-003 | 참여자는 `attendees` 그대로이다. 주관자와 연결된 발화자가 `attendees`에 없으면 중단한다. |
| IN-004 | `meeting_id`는 `m-{회의 날짜}-{요청 해시 4자리}`이다. 같은 요청을 다시 보내면 실패한 단계부터 진행한다. |
| IN-005 | 변환 결과를 입력 파서로 다시 검사하고, 발화 수가 달라지면 중단한다. |
| IN-006 | 메타데이터는 첫 빈 줄까지만 읽는다. |
| IN-007 | 입력 오류는 422, 실행 실패는 502로 응답하고, 실패 응답에 내부 정보를 넣지 않는다. |

주관자는 입력 텍스트에 넣지 않는다. 폴더 · 회의 종류와 함께 PostgreSQL 테이블이 정해지면 저장 방식을 정한다.
