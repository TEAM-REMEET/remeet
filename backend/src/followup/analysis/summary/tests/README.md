# 요약 에이전트 평가

요약 에이전트([summarize_meeting.py](../../../application/summarize_meeting.py): 생성 → 검증 ⇄ 재수정)가 회의를 빠짐없이, 원문에 충실하게 요약하는지 점수로 확인한다.
정답셋은 사람이 만든 **안건 구간 정답**(TS3003 a~d)을 쓴다.

> 이 테스트는 **실제 OpenAI를 호출한다.** 회의 하나당 요약 호출이 최대 7번(생성 1~2 · 검증 1~3 · 재수정 0~2) 나가고, 돌릴 때마다 점수가 조금씩 바뀐다.
> **결과는 저장하지 않는다** (`save=False`, 단계별 응답 캐시도 끔).

## 데이터

| 무엇 | 위치 |
|---|---|
| 회의 원문 | `evals/datasets/TS3003a~d/TS3003x.txt` |
| 정답셋 | `playground/chaewon/dataset/GoldenSet/hj/TS3003x.json` — 구간 경계(`boundary_positions`), 구간별 `subject` · `summary` |

- 원문의 메타데이터 3줄은 파이프라인 입력 형식이 아니라서 `conftest.transcript_text`가 바꿔서 넣는다 (`회의 대주제` → `회의 제목`, 참여자 `역할` → `역할_역할_u1`). 본문은 그대로이므로 **발화 번호 = 정답셋 position**.
- 요약을 돌리기 전에 발화 수가 정답셋 `utterance_count`와 같은지 먼저 확인하고, 다르면 LLM 호출 없이 실패한다.
- 정답셋에는 결정사항 · 액션 정답이 없다. 그래서 결정 · 액션 Recall/Precision은 아직 재지 않는다.

## 파일 구성

| 파일 | 하는 일 | 결과 |
|---|---|---|
| `conftest.py` | 원문 변환, 정답셋 읽기, 실제 LLM 연결, 호출 수 · 시간 측정, 가짜 LLM | — |
| `test_quantitative.py` | 정답 구간과 비교 (커버리지 · 경계 일치), 근거 연결률, 검증 통과율 | % 점수 |
| `test_scenarios.py` | 정해진 상황 5개에서 올바르게 동작하는지 | 통과/실패 |
| `test_qualitative.py` | LLM이 원문 충실도 · 정답 주제 커버리지를 채점 (deepeval) | 0~1 점수 |

## 준비

1. **`remeet/.env`** 에 `OPENAI_API_KEY`, `OPENAI_MODEL`
2. **패키지 설치** (`remeet` 폴더에서): `uv pip install pytest deepeval` (`deepeval`은 `test_qualitative.py`에만 필요)
3. **평가할 회의**: 기본값 `TS3003a,TS3003b,TS3003c,TS3003d`. 정답셋이 있는 다른 회의(ES2004a 등)도 원문이 `evals/datasets`에 있으면 넣을 수 있다.

## 실행

`remeet` 폴더에서 실행한다. **`-s`를 꼭 붙여야** 점수가 화면에 찍힌다.

PowerShell
```powershell
$env:PYTHONPATH = "backend/src"
$env:SUMMARY_EVAL_MEETINGS = "TS3003a"   # 생략하면 TS3003 a~d
uv run pytest backend/src/followup/analysis/summary/tests -s
```

Git Bash
```bash
PYTHONPATH=backend/src SUMMARY_EVAL_MEETINGS="TS3003a" \
  uv run pytest backend/src/followup/analysis/summary/tests -s
```

- 다른 tests 폴더(onboarding · recommend)와 **한 명령으로 같이 돌리지 않는다** (`conftest` 이름 충돌). 폴더별로 따로 실행한다.
- 같은 명령으로 세 파일을 같이 돌리면 회의별 요약은 **한 번만** 만들고 공유한다. 파일을 하나씩 돌리면 그때마다 새로 만든다.

## 결과 읽는 법

### test_quantitative.py

```
TS3003a: agendas=6 (gold segments=8) coverage=7/8 boundary=5/7 evidence=31/31 status=PASSED calls=3 latency=95.2s
  missed: ['회의 시작과 진행 준비']
...
Coverage: 90.6%, boundary match: 71.4%, evidence: 100.0%, verify passed: 3/4, mean LLM calls: 3.5, mean latency: 101.3s
```
(숫자는 예시)

| 항목 | 뜻 | 통과 기준 |
|---|---|---|
| coverage | 정답 구간 중, 그 구간의 발화를 근거로 쓴 요약 항목이 하나라도 있는 비율 | 70% 이상 |
| boundary | 정답 경계 ±10발화 안에서 시작하는 안건이 있는 비율 | 기록만 함 |
| evidence | 근거 발화가 붙은 항목 비율 | 95% 이상 |
| status / verify passed | 검증 결과 `PASSED` 또는 `REVIEW_REQUIRED` | 기록만 함 |
| calls / latency | 회의당 LLM 호출 수 · 처리 시간 | 기록만 함 |

`missed`는 요약이 다루지 않은 정답 구간의 주제다. 통과 기준은 파일 맨 위 상수에서 바꾼다.

### test_scenarios.py

| 시나리오 | 만드는 상황 | 기대 동작 | 실제 LLM |
|---|---|---|---|
| s01 정상 | 첫 번째 회의 전체 | 안건이 있고, 근거 번호가 원문 범위 안, 회의 결과 ID가 실제 항목을 가리킴 | 호출 |
| s02 입력 형식 오류 | 데이터셋 원문을 변환 없이 넣음 | `TranscriptFormatError`, LLM 호출 0번 | 안 함 |
| s03 빈 생성 결과 | LLM이 빈 요약을 돌려줌 | 다시 호출한 뒤 `EmptySummaryError` | 안 함 |
| s04 LLM 실패 | LLM이 연결 오류를 냄 | 오류가 그대로 올라옴, 1번만 호출 | 안 함 |
| s05 발화 없음 | 메타데이터만 있는 원문 | `ValueError`, LLM 호출 0번 | 안 함 |

### test_qualitative.py

```
TS3003a Faithfulness: 0.92 (…채점 이유…)
TS3003a Coverage: 0.8 (…채점 이유…)
...
Faithfulness mean: 0.90, coverage mean: 0.78
```
- **Faithfulness**: 요약 문장이 원문에서 확인되는가 (지어낸 내용이 없는가). 원문 전체를 근거로 준다.
- **Coverage (GEval)**: 정답셋 구간의 주제 · 결론이 요약에 빠짐없이 있는가. 표현 · 안건 묶는 단위 차이와 인사 · 진행 안내 구간 누락은 감점하지 않는다.

둘 다 평균 0.7 이상이면 통과. 채점 모델은 `OPENAI_MODEL` (없으면 `gpt-4o-mini`).

## 점수가 기준보다 낮으면

- `발화 수 … ≠ 정답셋 …` → 원문과 정답셋의 버전이 다름. `evals/datasets` 원문의 줄 수를 정답셋 `source_transcript`와 맞춘다
- coverage가 낮음 → `missed` 주제를 보고 생성 프롬프트(`prompts/extract_summary_action_item.py`)의 요약 범위 점검
- evidence가 낮음 → 후처리가 잘못된 근거 번호를 지운 것. 요약 결과의 `postprocess_log` 확인
- Faithfulness가 낮음 → 채점 이유에 나온 문장을 원문과 대조. 검증 프롬프트(`prompts/verify_summary.py`)가 '원문에 없는 내용'을 잡는지 점검
- 정답셋을 프롬프트 튜닝에 반복해서 쓰면 점수만 오른다. 튜닝은 TS3003으로 하고 최종 확인은 다른 회의(예: ES2004)로 한다
