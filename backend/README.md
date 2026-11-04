# Python 백엔드

## 베이스 에이전트 실행

실행 흐름은 LangGraph `StateGraph`로 구성한다.

```text
START -> prepare -> summarize -> extract_actions
                       ^              |
                       +-- 다음 청크 --+
                                      |
                                모든 청크 완료
                                      v
                               merge_summary -> save_results -> END
```

`prepare`는 입력·프롬프트 로드와 분할, `summarize`와 `extract_actions`는 현재 청크의 독립 추출,
`merge_summary`는 최종 요약, `save_results`는 최종 파일 저장을 담당한다.
노드 실행 중 예외는 기존과 같이 실패로 기록하며 자동 재시도하지 않는다.
프로젝트 의존성에 LangGraph가 추가되었으므로 실행 환경에서 의존성을 설치해야 한다.

프로젝트 루트(`remeet/`)에서 Python 3.12 이상으로 실행한다. 프로젝트 의존성인 `python-dotenv`를 사용한다.
기본 모델은 기존 TS3003c 실험과 같은 `gpt-6-luna`이며, 추론 수준은 `low`이다.
`remeet` 바로 상위 폴더의 `.env`를 자동으로 읽는다. 파일에 `OPENAI_API_KEY=본인의_API_키`를 설정한다.
이미 설정된 환경변수는 `.env` 값보다 우선한다. 실행 위치와 관계없이 같은 파일을 읽으며, 키를 출력하거나 파일을 수정하지 않는다.

```powershell
python backend/run_base_agent.py
python backend/run_base_agent.py evals/datasets/TS3003c/TS3003c.txt --model gpt-6-luna --chunk-size 1000
python -m unittest discover -s backend/tests/unit -p "test_*.py"
```

- 입력은 UTF-8 TXT, DOCX, HWPX를 지원한다. 문서의 문단·표 텍스트를 읽으며 이미지/OCR은 지원하지 않는다.
- 메타데이터는 입력과 같은 위치의 `회의명.metadata.json`을 읽거나 `--metadata 경로`로 지정한다.
- 기본 데이터셋은 `evals/datasets/` 아래의 번역 회의 20개이다. 원본은 `playground/hyungjin/test/input/`에서 복사했고, 회의별 메타데이터 JSON도 함께 준비했다. 예: `python backend/run_base_agent.py evals/datasets/TS3003c/TS3003c.txt`.
- 글자 수는 줄바꿈·공백을 포함한 Python 문자열 길이로 계산하며, 겹침 없이 나눈다.
- 실행 결과는 `outputs/base-agent/회의명_실행시각/`에 저장한다. `--output 경로`로 새 폴더를 지정할 수 있으며 기존 폴더를 덮어쓰지 않는다.
- `run.json`에는 성공/실패, 실패 단계, 모델, 사용량, 입력 해시를 기록한다. 프롬프트·분할 원문·청크 결과도 실행 폴더에 보관한다.
- 최종 산출물은 `회의명_요약.md`, `회의명_액션.jsonl`이다. 액션이 없으면 JSONL은 빈 파일이다.
- 실패하면 종료 코드 1을 반환한다. 모델 입력 한도 초과도 재시도 없이 실패 처리한다.
- 형식 검사는 수행하지만 별도 검증 에이전트, ReAct, 중복 제거는 구현하지 않는다. 두 추출 경로는 독립적이며 순차 호출한다.
- 실행 폴더에는 전사록이 포함되므로 공개 저장소에 올리지 않는다. `outputs/`는 기존 Git 제외 규칙에 포함되어 있다.

## 채워야 할 내용

FastAPI API와 분석 Worker가 공유하는 Python 패키지 및 pyproject.toml을 작성한다.

## 구현 원칙

Neo4j·GCS를 영속 저장소로 사용한다. API는 요청/조회, Worker는 장시간 분석을 담당한다.

## 완료 기준

API 연결과 무관하게 분석이 진행되고 실패/재시작 후 저장된 상태로 복원된다.

