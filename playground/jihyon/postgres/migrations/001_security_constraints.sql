-- Apply ONLY after reviewing existing data; no DROP or destructive operations.
-- New rows must satisfy these checks. Existing dirty rows can remain until cleaned.
ALTER TABLE accounts ADD CONSTRAINT accounts_email_format_ck
    CHECK (email ~* '^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$') NOT VALID;
ALTER TABLE accounts ADD CONSTRAINT accounts_hash_format_ck
    CHECK (password_hash IS NULL OR password_hash LIKE 'pbkdf2_sha256$%') NOT VALID;
ALTER TABLE meeting_shares ADD CONSTRAINT meeting_shares_not_self_ck
    CHECK (recipient_user_id IS NULL OR recipient_user_id <> sender_id) NOT VALID;
-- These unique indexes fail if duplicates exist. Resolve duplicates before migration.
CREATE UNIQUE INDEX meeting_share_unique_active_user
    ON meeting_shares(meeting_id, recipient_user_id)
    WHERE revoked_at IS NULL AND recipient_user_id IS NOT NULL;
CREATE UNIQUE INDEX meeting_share_unique_active_team
    ON meeting_shares(meeting_id, recipient_team_id)
    WHERE revoked_at IS NULL AND recipient_team_id IS NOT NULL;

-- Existing values are not rewritten; only future status changes are validated.
CREATE FUNCTION remeet_validate_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = OLD.status THEN RETURN NEW; END IF;
  IF (OLD.status = 'review' AND NEW.status IN ('confirmed','fail'))
    OR (OLD.status = 'confirmed' AND NEW.status IN ('sending','fail','review'))
    OR (OLD.status = 'sending' AND NEW.status IN ('done','fail'))
    OR (OLD.status = 'fail' AND NEW.status = 'review') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '허용되지 않는 회의 상태 전환: % -> %', OLD.status, NEW.status
    USING ERRCODE = '23514';
END $$;
CREATE TRIGGER meetings_status_transition
    BEFORE UPDATE OF status ON meetings
    FOR EACH ROW EXECUTE FUNCTION remeet_validate_status_transition();
