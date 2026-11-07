-- 016: 결과 전달이 끝난(done) 회의도 외부 도구로 다시 보낼 수 있게 상태 전환 추가.
--   done → sending  (외부 도구 전송 · 다시 보내기 시작)
--   done → fail     (참석자 전달은 끝났는데 외부 도구 전송이 실패)
-- 나머지 규칙은 008 과 같음 (미리보기 draft → review 포함).
CREATE OR REPLACE FUNCTION remeet_validate_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = OLD.status THEN RETURN NEW; END IF;
  IF (OLD.status = 'draft' AND NEW.status = 'review')
    OR (OLD.status = 'review' AND NEW.status IN ('confirmed','fail'))
    OR (OLD.status = 'confirmed' AND NEW.status IN ('sending','fail','review'))
    OR (OLD.status = 'sending' AND NEW.status IN ('done','fail'))
    OR (OLD.status = 'fail' AND NEW.status IN ('review','sending','done'))
    OR (OLD.status = 'done' AND NEW.status IN ('sending','fail')) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '허용되지 않는 회의 상태 전환: % -> %', OLD.status, NEW.status
    USING ERRCODE = '23514';
END $$;
