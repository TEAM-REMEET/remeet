-- 022: 전송 실패 기록에 「무엇이 실패했는지」 (외부 도구 전송 · 다시 보내기 때 실패한 것만 보내려고)
--      Jira = 할 일 id (meeting_todos.id) · 메일 = 받는 사람 user_id
--      NULL = key 정보 없음 (연결 실패라 아무것도 못 보냄 · 시뮬레이션 도구 · 022 이전 기록) → 다시 보낼 때 전부
--      빈 목록은 쓰지 않음 (record_failure 가 거절) — 「없음」과 「모름」이 섞이지 않게

ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS failed_keys TEXT[];
