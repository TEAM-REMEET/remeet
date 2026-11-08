-- 021: AI(LLM) 사용량 · 비용 기록과 관리자 설정 (관리자 「비용 관리」 화면).
--
--   llm_usage     AI 호출 1번 = 1줄. 응답이 온 직후 서버가 추가만 함 (고치거나 지우지 않음)
--                 · meeting_id 는 회의 표와 묶지 않음: 회의록을 지우거나 분석이 중간에 실패해도 쓴 비용은 남아야 해서
--                 · input_tokens 는 캐시 입력을 포함한 전체 입력, cached_tokens 는 그중 캐시로 처리된 입력
--                 · output_tokens 는 추론 토큰을 포함한 전체 출력
--                 · cost_usd 는 저장할 때의 단가(settings.LLM_PRICES_USD)로 계산. 단가가 없는 모델이면 NULL (화면에 「일부 비용 미반영」)
--   app_settings  관리자가 정하는 조직 설정 (이름 → 값). 예산 · 알림 기준 · 환율을 받지 못했을 때 쓰는 기본 환율
--
-- 사람 · 단계 · 모델 칸은 두지 않음 (사용자별 한도를 제공하지 않고, 쓰는 모델이 하나라서).

CREATE TABLE IF NOT EXISTS llm_usage (
  id            bigserial PRIMARY KEY,
  created_at    timestamptz NOT NULL DEFAULT now(),
  meeting_id    text,
  feature       text NOT NULL CHECK (feature IN ('summary', 'evidence', 'recommend', 'ask')),
  input_tokens  integer NOT NULL CHECK (input_tokens >= 0),
  cached_tokens integer NOT NULL DEFAULT 0 CHECK (cached_tokens >= 0),
  output_tokens integer NOT NULL CHECK (output_tokens >= 0),
  cost_usd      numeric(12, 6) CHECK (cost_usd >= 0)
);
CREATE INDEX IF NOT EXISTS llm_usage_created_at ON llm_usage (created_at);

CREATE TABLE IF NOT EXISTS app_settings (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by text
);

-- 기본값: 월 예산 50,000원 · 알림 기준 80% · 기본 환율 1,400원 (이미 있으면 그대로 둠)
INSERT INTO app_settings (key, value) VALUES
  ('monthly_budget_krw', '50000'),
  ('budget_alert_pct', '80'),
  ('usd_krw_rate', '1400')
ON CONFLICT (key) DO NOTHING;

-- 앱 계정: 사용량은 추가 · 읽기만 (관리자 API에서만 읽음), 설정은 읽기 · 저장
GRANT SELECT, INSERT ON llm_usage TO remeet_app;
GRANT USAGE ON SEQUENCE llm_usage_id_seq TO remeet_app;
GRANT SELECT, INSERT, UPDATE ON app_settings TO remeet_app;
