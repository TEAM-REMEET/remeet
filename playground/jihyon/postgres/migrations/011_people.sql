-- 011: 구성원 표 (사람 · 팀 · 직무 · 스킬). Neo4j Person · Team · Role · Skill 과 같은 내용 (seed_people.py · crud/people.py 가 채움)
--
--   schema.sql 에는 넣지 않음 (schema.sql 주석: 구성원 표는 팀원 담당이라 거기서 만들지도 지우지도 않음).
--   그래서 seed.py 로 다른 표를 다시 만들어도 이 표들과 데이터는 남고, IF NOT EXISTS 라 다시 적용해도 안전.
--   people.user_id 는 accounts 에 외래 키를 걸지 않음: schema.sql 이 accounts 를 DROP ... CASCADE 할 때
--   외래 키만 조용히 사라져서, 걸어 둬도 seed.py 한 번이면 효과가 없어짐. (계정 · 프로필 일치는 sync 코드가 맞춤)
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
    user_id    TEXT PRIMARY KEY,                    -- = Neo4j Person.user_id = accounts.user_id
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
