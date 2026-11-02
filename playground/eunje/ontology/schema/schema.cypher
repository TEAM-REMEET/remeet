CREATE CONSTRAINT IF NOT EXISTS FOR (n:Project)    REQUIRE n.project_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Topic)      REQUIRE n.topic_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Meeting)    REQUIRE n.meeting_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:AgendaItem) REQUIRE n.agenda_item_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:ActionItem) REQUIRE n.action_item_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Person)     REQUIRE n.user_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Team)       REQUIRE n.team_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Role)       REQUIRE n.role_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Skill)      REQUIRE n.skill_id IS UNIQUE;

// BGE-M3 = 1024차원
CREATE VECTOR INDEX topic_embedding IF NOT EXISTS
FOR (t:Topic) ON t.embedding
OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}};

CREATE VECTOR INDEX agenda_embedding IF NOT EXISTS
FOR (a:AgendaItem) ON a.embedding
OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}};