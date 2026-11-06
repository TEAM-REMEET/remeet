-- Safe non-destructive migration for pre-existing PostgreSQL databases.
-- Run ONCE with psql; never run schema.sql against data you need to keep.
ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'USER';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname='accounts_role_check'
          AND conrelid='accounts'::regclass
    ) THEN
        ALTER TABLE accounts ADD CONSTRAINT accounts_role_check
        CHECK (role IN ('USER','ADMIN'));
    END IF;
END $$;
