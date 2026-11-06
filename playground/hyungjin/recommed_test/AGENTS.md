# 회의 지식자산화 실험 지침

이 디렉터리에서 회의 지식을 Markdown 노드와 링크로 관리하고, Topic 연결·질의응답·담당자 추천을 실험한다. 상세 실험과 평가는 `계획서.md`를 따른다. `raw/`의 원본은 수정하지 않는다. 실험에 필요한 정보가 없으면 미상으로 남기고 만들어내지 않는다.

## 조회 방식

- 아래 스키마를 기준으로 질문을 노드·속성·관계 방향·필터·반환값이 명시된 Cypher 형태의 조회로 표현한 뒤, 해당 조건에 맞는 Markdown 페이지와 링크를 찾아 결과를 모은다. 필요하면 읽기 전용 파일 검색으로 유형별 페이지를 찾고 링크를 따라간다.
- 현재 Markdown용 Cypher 실행기는 없다. 실제로 실행하지 않은 Cypher를 실행했다고 말하지 않는다. 에이전트가 파일을 읽어 조건을 직접 대조한 경우 그렇게 기록한다. 지원하지 않는 조건, 누락된 페이지나 불확실한 ID는 오류 또는 미상으로 표시한다. 빈 조회 결과와 조회 실패는 구분한다.
- 실행별로 질문, 조회 의도 또는 질의, 확인한 페이지, 반환 결과·누락·오류와 최종 판단을 기록한다. ID는 이름과 구분하고, 모든 답변과 판단에 근거 페이지 및 가능한 회의 발화 번호를 붙인다.
- 기존 PR #61의 담당자 추천은 LLM이 Cypher를 생성하는 방식이 아니라 코드에 고정된 쿼리를 호출한다. 여기의 자연어 → Cypher 형태 조회는 Markdown 실험을 위한 새 접근이다.

## 스키마

노드 하나는 ID를 파일명으로 하는 페이지 하나다. `wiki/people/`, `teams/`, `roles/`, `skills/`에 조직 데이터가 있으며, 회의 처리 시 `projects/`, `topics/`, `meetings/`, `agendas/`, `discussions/`, `actions/`에 같은 방식으로 만든다. 관계는 출발 노드 페이지에 `관계명 → [[대상ID]]`와 관계 속성을 기록한다. 링크 검색으로 역방향 관계도 조회할 수 있다. 유형과 ID가 같은 페이지가 둘 이상이면 오류로 처리한다.

| 노드 | 주요 속성 |
| --- | --- |
| Project | project_id, name, status |
| Topic | topic_id, title, status(open/resolved/parked), last_discussed_at |
| Meeting | meeting_id, title, held_at |
| Agenda | agenda_id, order, title, background, decisions, unresolved_issues, next_topics |
| Discussion | discussion_id, order, title, discussions, evidence, discussion_tag, decision_tag |
| ActionItem | action_item_id, description, status(open/completed), due |
| Person | user_id, name, is_active, position(junior/mid/senior) |
| Team | team_id, name, is_active |
| Role | role_id, name |
| Skill | skill_id, name |

관계 방향은 다음과 같다. 화살표 오른쪽이 대상이다.

```text
Topic -IN-> Project                 Meeting -IN-> Project
Agenda -ABOUT-> Topic              Person -ATTENDED-> Meeting
Person -RECOMMENDED_FOR-> ActionItem
Person -ASSIGNED_TO-> ActionItem   Team -OWNS-> Project
Meeting -HAS_AGENDA-> Agenda       Agenda -HAS_DISCUSSION-> Discussion
Discussion -PRODUCED-> ActionItem  ActionItem -REQUIRES_SKILL-> Skill
Person -MEMBER_OF-> Team           Person -HAS_ROLE-> Role
Person -HAS_SKILL-> Skill
```

`ABOUT`에는 confidence, linked_by(auto/hitl), 판단 이유를 보존한다. `HAS_SKILL`에는 level과 updated_at을, `REQUIRES_SKILL`에는 importance를 기록한다. `RECOMMENDED_FOR`에는 status와 점수 및 각 점수 구성요소를, `ASSIGNED_TO`에는 work_status와 assign_source를 기록한다. 원본에 없는 updated_at·업무 이력은 미상이다. Discussion 안의 여러 논의 문장마다 태그와 발화 근거를 서로 대응되게 보존하고, `액션` 태그 문장은 ActionItem과 연결한다. `액션`은 일반 논의 태그 목록과 달리 액션 추출을 위한 입력 값이다.

## Topic 연결

기준 구현은 `../../eunje/ontology/scripts/load.py`의 `link_topic`과 `TOPIC_JUDGE_SYSTEM`이다.

1. 안건의 프로젝트를 확인하고, 같은 프로젝트에 속한 `open`·`parked` Topic을 조회한다. 각 Topic의 최근 연결 안건 최대 3건의 제목·결정·next_topics와 이번 안건의 제목·배경·결정·미해결 이슈를 확인한다. 시간은 회의 시각, 회의 ID, 안건 순서로 정렬한다.
2. 기존 논의의 연속이나 관련 문제·대상이면 기존 Topic을 제안한다. 다음 회의 주제에 예고된 내용도 고려하되, 넓은 주제에 모든 안건을 몰아넣지 않는다. 판단이 어려우면 새 Topic을 제안하고 검토 필요 이유를 남긴다.
3. 기존 연결은 실제 후보 ID인지 검증한다. 새 Topic은 `tp-{agenda_id}`를 기준으로 중복 여부를 확인하고 제목과 `open` 상태를 기록한다. `Agenda -ABOUT-> Topic`과 `Topic -IN-> Project`를 만들고 마지막 논의 시각을 갱신한다.
4. 기존 구현은 기존 연결의 confidence를 비워 두고 새 Topic에 1.0을 기록한다. 이 1.0은 자기 연결을 표시하는 관례이지 확률이 아니다. 사람의 수정은 `linked_by: hitl`로 남기고 자동 처리로 덮어쓰지 않는다.
5. 모델 출력이 잘못되거나 조회가 실패하면 새 Topic으로 자동 확정하지 말고 오류 및 검토 대기 상태로 남긴다. 이는 PR #61의 형식 오류 시 새 Topic 생성과 다른 실험상 안전 처리다. 이유와 후보·근거를 기록한다.

## 담당자 추천

기준 구현과 세부 함수는 `../../eunje/ontology/scripts/recommend_agent.py`, 설명은 `../../eunje/ontology/scripts/recommend_agent.md`다. 담당자가 확정되지 않은 `open` ActionItem을 대상으로 한다. 이미 확정된 담당자를 변경하지 않으며 추천은 배정이 아니다.

1. 액션에서 Discussion → Agenda → Meeting → Project, 관련 Topic과 참석자까지 역방향으로 조회한다. 원문 담당자 표현은 확정 담당자와 구분한다. 회의에서 명시한 사람이 식별되면 추천으로 덮어쓰지 않고 검토 대상으로 표시한다.
2. Skill·Role·Team 목록과 활성 인물, 각자의 스킬 숙련도·팀·역할·미완료 업무를 조회한다. 회의 맥락을 토대로 필요한 스킬과 중요도(core/major/minor), 목표 역할·팀을 제안하고 실제 존재하는 ID인지 검증한다. 판단 근거가 없는 조건을 사실로 만들지 않는다.
3. PR #61을 참고한 점수는 스킬 0.5, 역할·팀 0.2, 업무량 0.3이다. 스킬 점수는 `100 × Σ(정규화한 중요도 × 숙련도/4)`, 중요도는 core 0.5, major 0.3, minor 0.2다. 역할·팀은 직접 일치 100, 목표 역할과 팀이 모두 지정된 경우 둘 중 하나만 일치하면 70, 그 외 0이다. 인접 역할은 추론하거나 점수에 반영하지 않는다. 업무량은 `100/(1+미완료 업무 수)`다. 목표 역할·팀이 없으면 스킬과 업무량의 가중치를 0.8로 나눈다. 동일 점수는 스킬, 업무량, user_id 순으로 정렬한다. 비활성자와 필수 조건 불일치자는 사유를 기록하고 제외한다. 기존 코드의 인접 역할 처리는 이 실험에서 적용하지 않는다.
4. 원본 추천 로직은 직급별 최대 2명의 후보를 뽑고 메인 추천은 senior 1, mid 1, junior 2다. 부족한 직급은 비우고 경고한다. 단, 현재 더미 데이터에는 업무 이력이 없어 0건이라고 가정하거나 업무량 점수·총점을 확정하지 않는다. 업무량 미상과 기준 알고리즘 실행 불가를 표시하고, 알려진 스킬·역할 근거만 별도로 제시한다. 가상 업무 이력이 명시적으로 준비되면 기준 계산을 실행할 수 있다.
5. 결과에 액션·후보 ID, 확인된 점수와 산출 근거, 제외 사유, 출처, 누락 정보 및 검토 필요 사항을 남긴다. 요구 스킬 판단 불가, 후보 없음, 조회 오류를 구분한다. 추천 관계는 `pending` 제안으로 기록하며 `ASSIGNED_TO`를 만들지 않는다. 기존 accepted/rejected 추천 및 수동 확정은 보존한다.

## 기록과 검증

회의별 입력의 ID·순서·근거를 보존한다. 이미 존재하는 노드와 관계는 ID를 대조하고 중복 생성하지 않는다. 변경 전 기존 링크와 검토 상태를 확인하고, `wiki/log.md`에 처리·수정 결과를 추가한다. Topic 연결 제안과 추천 결과는 `outputs/`에 조회와 근거를 포함해 남긴다. 요청된 작업이 허용하는 범위에서만 해당 페이지를 갱신한다.

실행 후 생성된 ID와 링크 대상, 관계 방향, 원문 근거, 계산 가능 여부를 확인한다. 설명에는 관찰된 사실·제안·미상 정보를 구분하고 실제로 실행하지 않은 조회나 평가를 완료했다고 말하지 않는다.
