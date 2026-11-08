# 요약 · 액션 아이템 추출 — 구현 계획

결정 배경: [단일 생성 프로세스 채택](../../docs/decisions/2026-11-05-summary-action-single-pass.md)

## 구조

| 위치 (`backend/src/followup/`) | 역할 |
|---|---|
| `settings.py` | 조정 값 (키 · 모델 이름은 환경변수) |
| `analysis/summary/schemas.py` | LLM 출력 · `summary.json` 스키마 |
| `analysis/summary/preprocessing.py` | 전사문 파싱 · 참여자 이름 처리 |
| `analysis/summary/steps.py` | 생성 · 검증 · 재수정 단계와 최종 상태 판정 |
| `analysis/summary/postprocess.py`, `analysis/summary/revision.py` | 근거 검사 · ID 부여, 재수정 적용 |
| `analysis/summary/prompts/` | 프롬프트 |
| `application/summarize_meeting.py` | LangGraph 흐름 · 호출 상한 · 단계별 응답 재사용 |
| `application/ports.py` | LLM · 저장소 경계 |
| `adapters/llm/structured_client.py`, `adapters/llm/tracing.py` | OpenAI 구조화 출력, LangSmith |
| `adapters/storage/local_files.py` | 로컬 파일 저장 |
| `entrypoints/bootstrap.py`, `entrypoints/cli.py` | 구현체 연결, 파일 실행 |

## 의존성

`openai`, `pydantic`을 `backend/requirements.txt`에 추가했다. `langsmith`는 선택이며 없어도 동작한다.

## 배포

`deploy/docker-compose.yml`의 `backend`에 `backend_outputs:/app/outputs` 볼륨을 추가했다. 재시작 후에도 결과와 단계별 응답이 유지된다.

## 검증

- **단위 테스트:** 가짜 LLM으로 `backend/tests/unit`에서 실행한다.
- **품질:** 실제 모델 품질은 `evals`에서 측정하며, 이 명세의 테스트에 포함하지 않는다.
