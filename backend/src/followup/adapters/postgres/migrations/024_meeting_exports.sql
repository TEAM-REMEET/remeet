-- 024: 회의별 외부 도구 전송 성공 기록 (회의록 확정 6단계 「외부 도구 전송」을 마쳤는지)
--
-- 예전에는 보내기에 성공하면 integration_tools.last_sync_at (도구별 마지막 전송 시각) 하나만 바뀌어서
-- 「결과 전달만 하고 멈춘 회의」와 「외부 도구까지 보낸 회의」가 DB 에서 똑같이 status = 'done' 이었어요.
-- 이제 성공(record_export) · 다시 보내서 해결(resolve_failure) 때 (회의, 도구) 한 행을 남겨요.
--   화면: 회의 상세 exported_at 이 비어 있고 상태가 done 이면 「외부 도구 전송 전」
-- 실패는 지금처럼 failed_exports 에 (022). 같은 도구로 다시 보내면 시각만 새로.

CREATE TABLE IF NOT EXISTS meeting_exports (
  meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  tool_id     TEXT NOT NULL REFERENCES integration_tools(id) ON DELETE CASCADE,
  exported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  exported_by TEXT REFERENCES accounts(user_id) ON DELETE SET NULL,
  PRIMARY KEY (meeting_id, tool_id)
);
