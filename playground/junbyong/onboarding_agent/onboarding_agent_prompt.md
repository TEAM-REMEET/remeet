# 회의록 기반 온보딩 질의 에이전트 구현 프롬프트

> 이 문서는 코딩 에이전트(Claude Code 등)에게 그대로 전달하는 구현 지시서입니다. `[확인 필요]` 표시는 실제 프로젝트 환경에 맞게 채우거나, 코딩 에이전트가 작업 전에 사용자에게 물어봐야 하는 항목입니다.

---

## 0. 역할과 목표

당신은 Python, LangGraph, Neo4j에 능숙한 백엔드 엔지니어입니다. 이미 구축된 **회의록 기반 Follow-up AI Agent**의 Neo4j 그래프 위에 다음 두 가지를 구현합니다.

1. **지식자산화 레이어**: 과거 회의(AgendaItem)에서 결정사항(Decision)과 용어(Term)를 추출해 그래프에 적재하고, 사람–토픽 기여도(CONTRIBUTED_TO)를 계산한다.
2. **온보딩 질의 에이전트**: 신입사원과 이직자가 업무 파악, 그동안의 진행 내용, 용어, 결정 배경, 문의할 담당자를 자연어로 묻고 **근거가 달린 구조화된 답변**을 받는 LangGraph 에이전트.

핵심 원칙

- Neo4j가 지식 저장소이자 검색 엔진이다. LLM은 **질문 해석과 답변 문장화**만 담당한다.
- 모든 답변은 그래프의 근거 노드(Meeting / AgendaItem)를 인용해야 하며, 근거가 없으면 "모른다"고 답하고 담당자를 안내한다.
- 대체된(superseded) 결정을 현재 유효한 것처럼 말하지 않는다.

---

## 1. 기술 스택

| 항목 | 사용 기술 |
| --- | --- |
| 언어 | Python 3.11+ |
| 에이전트 | LangGraph (기존 담당자 추천 에이전트와 동일한 스타일 유지) |
| 그래프 DB | Neo4j 5.x (vector index 사용), 공식 `neo4j` Python driver |
| LLM / 임베딩 | `[확인 필요]` 기존 프로젝트에서 쓰는 모델과 임베딩 차원 그대로 사용 |
| 구조화 출력 | Pydantic v2 모델 + LLM structured output |
| API | `[확인 필요]` FastAPI 권장 |
| 선택 | Neo4j GDS (PageRank, Louvain, Node Similarity) |

작업 시작 전에 기존 코드(특히 `load.py`, 담당자 추천 에이전트)의 Neo4j 연결 방식, 설정 파일, LLM 클라이언트를 먼저 읽고 **같은 패턴을 재사용**하세요.

---

## 2. 현재 그래프 스키마 (변경 금지, 확장만 허용)

### 2.1 노드

| 영역 | 노드 | 주요 속성 | 적재 방식 |
| --- | --- | --- | --- |
| 프로젝트 | `Project` | project_id, name, status | MERGE |
| 프로젝트 | `Topic` | topic_id, title, status(open/resolved/parked), summary(누적 요약), last_discussed_at, embedding | MERGE |
| 회의 | `Meeting` | meeting_id, title, held_at | CREATE |
| 회의 | `AgendaItem` | agenda_item_id, order, title, background, discussion, decisions\[\], next_topics\[\], embedding | CREATE |
| 회의 | `ActionItem` | action_item_id, description, status(open/in_progress/completed), due, owner_raw | CREATE |
| 사용자 DB | `Person` | user_id, name, is_active, position(junior/mid/senior) | 동기화 |
| 사용자 DB | `Team` | team_id, name, is_active | 동기화 |
| 사용자 DB | `Role` | role_id, name | 동기화 |
| 사용자 DB | `Skill` | skill_id, name | 동기화 |

### 2.2 관계

```
(Topic)-[:IN]->(Project)
(Topic)-[:SUBTOPIC_OF]->(Topic)
(Meeting)-[:IN]->(Project)
(Team)-[:OWNS]->(Project)
(Meeting)-[:HAS_AGENDA]->(AgendaItem)
(AgendaItem)-[:ABOUT {confidence, linked_by: auto|hitl}]->(Topic)
(AgendaItem)-[:PRODUCED]->(ActionItem)
(ActionItem)-[:FOLLOWS_UP]->(ActionItem)
(ActionItem)-[:REQUIRES_SKILL {importance}]->(Skill)
(Person)-[:ATTENDED]->(Meeting)
(Person)-[:RECOMMENDED_FOR {status, score, skill_score, role_team_score, workload_score}]->(ActionItem)
(Person)-[:ASSIGNED_TO {work_status: in_work|work_completed, assign_source: explicit|history|manual}]->(ActionItem)
(Person)-[:MEMBER_OF]->(Team)
(Person)-[:HAS_ROLE]->(Role)
(Person)-[:HAS_SKILL {level: 1..4, updated_at}]->(Skill)
```

> 관계 방향이 실제 DB와 다르면 **실제 DB를 기준**으로 하고, 다른 부분을 사용자에게 알려주세요. 작업 시작 시 `CALL db.schema.visualization()`으로 확인할 것.

---

## 3. 스키마 확장 (지식 레이어)

### 3.1 Decision 노드 (`AgendaItem.decisions[]` 승격)

```
(:Decision {
  decision_id, content, decided_at,
  status: 'active' | 'superseded' | 'reverted',
  confidence: float,
  verified_by: 'auto' | 'hitl',
  embedding
})
(AgendaItem)-[:DECIDED]->(Decision)
(Decision)-[:ABOUT]->(Topic)
(Decision)-[:SUPERSEDES {reason, detected_by: auto|hitl}]->(Decision)
```

- 기존 `decisions[]` 배열 속성은 **삭제하지 말고 유지** (마이그레이션 완료 후 제거 여부는 사용자가 결정).

### 3.2 Term 노드

```
(:Term {
  term_id, name, aliases: [string],
  definition, definition_status: 'draft' | 'verified',
  first_seen_at, mention_count, embedding
})
(Term)-[:DEFINED_IN {evidence}]->(AgendaItem)
(Term)-[:MENTIONED_IN]->(AgendaItem)
(Term)-[:RELATED_TO]->(Topic)
(Term)-[:SCOPED_TO]->(Project)
```

### 3.3 Person–Topic 기여도 (집계 관계)

```
(Person)-[:CONTRIBUTED_TO {attended_cnt, completed_action_cnt, last_at, score}]->(Topic)
```

- 근거 경로 1: `Person-ATTENDED->Meeting-HAS_AGENDA->AgendaItem-ABOUT->Topic`
- 근거 경로 2: `Person-ASSIGNED_TO{work_status:'work_completed'}->ActionItem<-PRODUCED-AgendaItem-ABOUT->Topic`
- score 공식은 설정값으로 분리: 기본 `score = 1.0*attended_cnt + 2.0*completed_action_cnt + 최신성 가중치`
- 가능하면 담당자 추천 에이전트의 점수 계산 유틸을 재사용

### 3.4 접근 제어

- `Meeting.visibility: 'public' | 'team' | 'restricted'` 추가 (기본값 `public`, `[확인 필요]` 정책)
- **모든 조회 Cypher는 visibility 필터를 반드시 포함**한다. LLM에 전달되기 전 단계에서 걸러야 함.

### 3.5 제약조건과 인덱스

```cypher
CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:Decision) REQUIRE d.decision_id IS UNIQUE;
CREATE CONSTRAINT term_id IF NOT EXISTS FOR (t:Term) REQUIRE t.term_id IS UNIQUE;
CREATE INDEX term_name IF NOT EXISTS FOR (t:Term) ON (t.name);
CREATE FULLTEXT INDEX term_alias_ft IF NOT EXISTS FOR (t:Term) ON EACH [t.name, t.definition];
CREATE VECTOR INDEX term_emb IF NOT EXISTS FOR (t:Term) ON (t.embedding)
  OPTIONS {indexConfig: {`vector.dimensions`: $DIM, `vector.similarity_function`: 'cosine'}};
CREATE VECTOR INDEX decision_emb IF NOT EXISTS FOR (d:Decision) ON (d.embedding) OPTIONS {...};
// Topic, AgendaItem vector index가 없으면 같이 생성
```

---

## 4. 구현 1: 지식 추출 및 백필 파이프라인

### 4.1 처리 순서 (반드시 `Meeting.held_at` 오름차순)

1. AgendaItem을 시간순으로 순회 (배치, 이미 처리된 건은 `processed_at` 속성 등으로 건너뛰어 \*\*재실행해도 안전(idempotent)\*\*하게)
2. LLM 구조화 추출: `background`, `discussion`, `decisions[]` → Decision 후보, Term 후보
3. Term 정합 (entity resolution)
   - name 또는 aliases와 정확히 일치하면 기존 노드에 병합
   - 아니면 임베딩 유사도 ≥ `TERM_MERGE_THRESHOLD`(기본 0.88)일 때 병합 후보
   - 0.75 \~ 0.88 구간은 HITL 큐로 보냄
   - 병합 시 aliases 추가, mention_count 증가, definition 누적 갱신 (Topic.summary와 같은 방식)
4. Decision 충돌 검사
   - 같은 Topic의 `status:'active'` Decision들과 비교
   - LLM이 `supersedes | complements | unrelated` 중 하나로 판정
   - `supersedes`이면 `SUPERSEDES` 관계 생성 + 기존 Decision을 `superseded`로 변경
   - confidence가 낮으면 HITL 큐로
5. 전체 완료 후 `CONTRIBUTED_TO` 일괄 재계산

같은 함수가 **새 회의 종료 시점에도 그대로 호출**되도록 설계 (백필 = 이 함수를 과거 데이터에 반복 적용).

### 4.2 LLM 추출 출력 스키마 (Pydantic)

```python
class ExtractedDecision(BaseModel):
    content: str            # 한 문장 결정 내용
    evidence: str           # discussion 원문 인용 (짧게)
    confidence: float       # 0~1

class ExtractedTerm(BaseModel):
    name: str
    aliases: list[str] = []
    definition: str | None  # 회의에서 정의가 명시되지 않았으면 None → MENTIONED_IN만 생성
    evidence: str | None

class AgendaExtraction(BaseModel):
    decisions: list[ExtractedDecision]
    terms: list[ExtractedTerm]

class SupersedeJudgement(BaseModel):
    relation: Literal["supersedes", "complements", "unrelated"]
    reason: str
    confidence: float
```

추출 프롬프트 규칙

- 회의 내용에 **명시된 것만** 추출. 일반 상식 용어(예: "회의", "일정")는 제외하고 프로젝트 고유 용어, 약어, 내부 명칭 위주로.
- 브레인스토밍성 발언은 Decision이 아님. 합의되거나 확정된 것만 Decision.
- evidence는 원문을 그대로 짧게 인용.

### 4.3 HITL 큐

- `(:ReviewTask {task_id, type: term_merge|supersede|decision, payload, status: pending|approved|rejected, created_at})` 노드 또는 별도 테이블 `[확인 필요: 기존 HITL 흐름이 있으면 그 방식을 따를 것]`
- 승인된 항목만 `verified_by:'hitl'`, `definition_status:'verified'`로 변경

---

## 5. 구현 2: 온보딩 질의 에이전트 (LangGraph)

### 5.1 그래프 흐름

```
classify_intent
  → extract_slots          (용어/토픽/프로젝트/기간/사용자를 Neo4j 노드 ID로 매핑)
  → route
      ├ term_retriever
      ├ decision_retriever
      ├ history_retriever
      ├ status_retriever
      ├ expert_retriever
      ├ my_work_retriever
      └ fallback_retriever (벡터 검색)
  → grounding_check        (근거 없는 주장 제거, superseded 경고)
  → compose_answer         (구조화된 OnboardingAnswer 생성)
  → suggest_followups      (사용된 노드의 1-hop 이웃 기반)
```

### 5.2 State

```python
class OnboardingState(TypedDict):
    question: str
    user_id: str                       # 질문자 (권한·팀 필터용)
    user_team_ids: list[str]
    intent: Literal["term","decision","history","status","expert","my_work","other"]
    slots: dict                        # term_ids, topic_ids, project_ids, date_from, date_to
    retrieved: list[dict]              # Cypher 결과 (노드 id, 텍스트, 출처)
    answer: "OnboardingAnswer | None"
    error: str | None
```

### 5.3 질문 유형별 탐색 경로

| intent | 예시 질문 | 탐색 |
| --- | --- | --- |
| term | "FU 에이전트가 뭐예요?" | Term(name/aliases/fulltext/vector) → DEFINED_IN → AgendaItem ← Meeting |
| decision | "왜 Neo4j를 써요?" | Topic → Decision(active) → SUPERSEDES\* 이력 → DECIDED ← AgendaItem.background |
| history | "지난 3개월간 뭐 했어요?" | Project ← IN ← Meeting(held_at 범위) → AgendaItem → Decision / 완료 ActionItem, 시간순 |
| status | "지금 진행 중인 건?" | Topic(status='open') + ActionItem(in_progress), last_discussed_at 내림차순 |
| expert | "이건 누구한테 물어봐요?" | Topic ← CONTRIBUTED_TO ← Person(is_active=true) |
| <u>my_work</u> | "제가 받은 업무의 배경이 뭐예요?" | Person(user_id) → ASSIGNED_TO → ActionItem ← PRODUCED ← AgendaItem → Topic → Decision(active) |
| other | 분류 불가 | Topic / AgendaItem / Decision 벡터 검색 → 1-hop 확장 |

### 5.4 Cypher 템플릿 규칙

- **LLM이 Cypher를 직접 생성하지 않는다 (Text2Cypher 금지).** LLM은 intent와 slot만 추출하고, 미리 정의한 템플릿에 파라미터를 바인딩해 실행한다.
- 템플릿은 `cypher/` 디렉터리 또는 `queries.py`에 이름을 붙여 관리.
- 모든 템플릿 공통 조건:
  - `m.visibility` 필터 (질문자의 팀/권한 기준)
  - Person은 `is_active = true`만
  - Decision 기본 조회는 `status = 'active'`, 이력 요청 시에만 superseded 포함
  - 결과 상한 `LIMIT` 필수
- 예시 (decision):

```cypher
MATCH (tp:Topic {topic_id: $topic_id})<-[:ABOUT]-(d:Decision {status:'active'})
MATCH (d)<-[:DECIDED]-(a:AgendaItem)<-[:HAS_AGENDA]-(m:Meeting)
WHERE m.visibility IN $allowed_visibility
OPTIONAL MATCH path = (d)-[:SUPERSEDES*1..5]->(old:Decision)
RETURN d.decision_id, d.content, d.decided_at, a.background, a.agenda_item_id,
       m.meeting_id, m.title, m.held_at,
       [n IN nodes(path)[1..] | {id: n.decision_id, content: n.content, decided_at: n.decided_at}] AS history
ORDER BY d.decided_at DESC LIMIT 5
```

- (선택) `other` intent 한정으로 Text2Cypher fallback을 붙이는 경우: **읽기 전용 Neo4j 계정**, 쓰기 키워드 차단, 실행 timeout 적용.

### 5.5 답변 출력 스키마 (UI 렌더링용)

```python
class SourceRef(BaseModel):
    meeting_id: str
    meeting_title: str
    held_at: str
    agenda_item_id: str
    agenda_title: str

class DecisionEntry(BaseModel):
    decision_id: str
    content: str
    decided_at: str
    status: Literal["active", "superseded", "reverted"]
    source: SourceRef

class ExpertRef(BaseModel):
    user_id: str
    name: str
    position: str
    reason: str          # 예: "추천 로직 설계 회의 5회 참석, 관련 액션 3건 완료"

class OnboardingAnswer(BaseModel):
    intent: str
    summary: str                         # 2~4문장 요약
    answer_status: Literal["grounded", "partial", "no_evidence"]
    verification: Literal["hitl", "auto", "mixed"]
    current_decision: DecisionEntry | None
    decision_history: list[DecisionEntry] = []
    terms: list[dict] = []               # {term_id, name, definition}
    sources: list[SourceRef]
    experts: list[ExpertRef] = []
    followups: list[str] = []            # 3개 이내 후속 질문
```

### 5.6 grounding_check 규칙

- summary의 각 주장이 `retrieved` 안의 노드에 근거하는지 확인. 근거 없는 문장은 제거.
- 사용된 Decision 중 superseded가 있으면 summary에 "이후 변경됨"을 명시.
- 근거가 전혀 없으면 `answer_status='no_evidence'`로 하고, summary에 "관련 회의 기록이 없어요"와 함께 expert_retriever 결과로 담당자를 안내.
- `definition_status='draft'`이거나 `verified_by='auto'`인 근거가 포함되면 `verification`에 반영.

### 5.7 시간 표현 정규화

- "최근", "지난달", "지난 분기", "올해 초" 등을 extract_slots 단계에서 `date_from` / `date_to`로 변환 (기준 시각은 서버 현재 시각, Asia/Seoul).

---

## 6. 구현 3: 온보딩 홈 데이터 API (질문 전 화면)

질문하기 전에 보여줄 데이터를 제공하는 엔드포인트.

| 엔드포인트 | 내용 | 쿼리 |
| --- | --- | --- |
| `GET /onboarding/home?user_id=` | 우리 팀 프로젝트, 진행 중 Topic Top 5, 핵심 용어 Top 10, 추천 질문 | Team-OWNS-&gt;Project&lt;-IN-Topic, Term 중요도 |
| `POST /onboarding/ask` | 질의 → `OnboardingAnswer` | LangGraph 실행 |
| `GET /onboarding/timeline?project_id=&from=&to=` | 월별 주요 Decision, 완료 ActionItem | history 템플릿 재사용 |
| `GET /onboarding/source/{agenda_item_id}` | 근거 칩 클릭 시 원문 (background, discussion) | 권한 필터 포함 |

- 핵심 용어 Top 10: 기본은 `mention_count` 기준, GDS가 있으면 Term–AgendaItem–Topic 투영 그래프에 PageRank 적용 (설정으로 전환).

---

## 7. 디렉터리 구조 (제안, 기존 구조가 있으면 그에 맞출 것)

```
onboarding/
  schema/
    migrations.cypher          # 제약조건, 인덱스, visibility 기본값
  knowledge/
    extractor.py               # LLM 추출 (AgendaExtraction)
    term_resolver.py           # Term 정합
    decision_linker.py         # SUPERSEDES 판정
    contribution.py            # CONTRIBUTED_TO 계산
    backfill.py                # CLI: python -m onboarding.knowledge.backfill --since 2026-01-01 --dry-run
  agent/
    state.py
    nodes/                     # classify_intent, extract_slots, retrievers, grounding, compose, followups
    queries.py                 # Cypher 템플릿 모음
    graph.py                   # LangGraph 조립
    schemas.py                 # Pydantic 모델
  api/
    routes.py
  eval/
    questions.yaml             # 유형별 평가 질문과 정답 노드 ID
    run_eval.py
  tests/
```

---

## 8. 작업 순서와 완료 기준

| 단계 | 작업 | 완료 기준 |
| --- | --- | --- |
| 1 | 기존 코드와 실제 DB 스키마 확인 | 스키마 차이점 리포트 출력, 사용자 확인 |
| 2 | 마이그레이션 (제약조건, 인덱스, visibility) | 재실행해도 에러 없음 |
| 3 | 추출기 + Term 정합 + Decision 연결 | 샘플 회의 3건에 dry-run 결과 출력 (DB 쓰기 없이) |
| 4 | 백필 실행 | 시간순 처리, 재실행 시 중복 노드 0, HITL 큐 건수 리포트 |
| 5 | CONTRIBUTED_TO 계산 | 퇴사자(is_active=false) 제외 확인 |
| 6 | Cypher 템플릿 7종 + 단위 테스트 | 각 템플릿이 visibility 필터를 포함하는지 테스트로 검증 |
| 7 | LangGraph 에이전트 조립 | 유형별 질문 1개씩 end-to-end 응답이 `OnboardingAnswer` 스키마 통과 |
| 8 | API | 4개 엔드포인트 동작 |
| 9 | 평가 | 유형별 5\~10문항, 근거 노드 적중률(Recall@k)과 no_evidence 정확도 리포트 |

---

## 9. 하지 말 것

- 기존 노드와 관계의 이름, 방향, 속성을 바꾸거나 삭제하지 않기 (추가만)
- `RECOMMENDED_FOR` 등 담당자 추천 에이전트의 데이터를 수정하지 않기
- LLM에 Cypher 생성을 맡기지 않기 (fallback 예외는 5.4 참고)
- visibility 필터 없이 회의 내용을 LLM에 넘기지 않기
- 근거 없는 내용을 답변에 넣지 않기, 퇴사자를 담당자로 안내하지 않기
- 백필을 dry-run 확인 없이 바로 실 DB에 쓰지 않기

## 10. 작업 전 사용자에게 확인할 것

1. LLM / 임베딩 모델과 임베딩 차원
2. visibility 정책 (팀 단위 제한 여부, restricted 회의 기준)
3. 기존 HITL 흐름의 구현 방식 (재사용 가능 여부)
4. API 프레임워크와 프론트엔드 연동 방식
5. Neo4j GDS 플러그인 사용 가능 여부
6. 백필 대상 기간과 회의 건수