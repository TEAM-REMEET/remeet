-- 사람 · 조직 · 스킬 표 추가 (schema.sql 의 같은 표와 동일). 표를 지우지 않음.
-- schema.sql 로 새로 만든 DB 에는 이미 있으므로 IF NOT EXISTS 로 건너뜀.
CREATE TABLE IF NOT EXISTS teams (
    team_id   TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(team_id)
);

CREATE TABLE IF NOT EXISTS skills (
    skill_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    domain      TEXT NOT NULL,
    tools       TEXT[] NOT NULL DEFAULT '{}',
    source      TEXT,
    ncs_code    TEXT,
    ncs_name    TEXT
);

CREATE TABLE IF NOT EXISTS people (
    user_id    TEXT PRIMARY KEY REFERENCES accounts(user_id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    position   TEXT NOT NULL CHECK (position IN ('junior', 'mid', 'senior')),
    team_id    TEXT NOT NULL REFERENCES teams(team_id),
    role_id    TEXT NOT NULL REFERENCES roles(role_id),
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS person_skills (
    user_id    TEXT NOT NULL REFERENCES people(user_id) ON DELETE CASCADE,
    skill_id   TEXT NOT NULL REFERENCES skills(skill_id),
    level      INT  NOT NULL CHECK (level BETWEEN 1 AND 5),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, skill_id)
);
