# 회의 경계 후보 및 LLM 검증 실험

전체 결론은 [최종 마무리 보고서](final_report.md)를 참고한다. 코드와 결과는 실험별로 함께 관리한다.

## 폴더 구성

| 폴더 | 실험 및 보존 자료 |
|---|---|
| `embedding_experiment/` | 임베딩·윈도우·후보 선택 비교. 계획, 실행 코드, 문장 분할 코드, 보고서, 집계 CSV, 데이터·실행 manifest, 선택된 BGE 후보 20개 |
| `merge_experiment/` | 범위 보존·중앙 재배치 병합과 복수 정답 분석. 실행·분석 코드, 보고서, 집계 CSV, manifest |
| `merge_experiment/uncapped_analysis/` | 길이 상한 없는 초기 병합 탐색 보고서와 CSV |
| `bge_distance_experiment/` | BGE 기존 점수를 재사용한 후보 병합 및 정답 거리 비교 |
| `llm_ts3003c/` | LLM 검증 계획, 현재 v3 실행 코드, handoff 도구, 버전 비교 보고서 |
| `llm_ts3003c/v1_boundary_options/` | v1 보고서·평가·사용량·최종 예측 |
| `llm_ts3003c/v2/` | v2 코드·계획·보고서·평가·사용량·최종 예측 |
| `llm_ts3003c/v3/` | v3 보고서·평가·사용량·최종 예측 |

최상위에는 이 안내와 최종 보고서만 둔다.

## 실험별 보고서

- [임베딩 후보 비교](embedding_experiment/report.md), [실험 계획](embedding_experiment/experiment_plan.md)
- [초기 무제한 병합](merge_experiment/uncapped_analysis/merged_candidate_analysis.md)
- [길이 제한 병합](merge_experiment/report.md), [묶음당 정답 분석](merge_experiment/gold_count_report.md)
- [BGE 경계 거리 2차 실험](bge_distance_experiment/report.md)
- LLM 검증: [v1](llm_ts3003c/v1_boundary_options/report.md), [v2](llm_ts3003c/v2/report.md), [v3](llm_ts3003c/v3/report.md)

임베딩 선택 설정은 BGE-m3 / 목표 200자 / 오버랩 100자 / 문장 보존 / depth ≥ 0.10이다.
후보 915개가 20개 회의의 정답 153개를 ±300자 범위로 포함했다. 이는 LLM 경계 추출 정확도와 구분한다.

## 재현 경로

저장소 루트의 `pyproject.toml`, `uv.lock`을 기준으로 환경을 구성한다. 임베딩 실행에 필요한 `psutil`도 설치되어 있어야 한다.
공통 전처리는 `../embedding-test/pipeline.py`, 입력은 `../test/input/`, 정답은 `../test/llm_golden/`을 사용한다.
데이터 목록과 해시는 `embedding_experiment/dataset_manifest.json`에 있다.
아래 명령은 이 README가 있는 폴더에서 실행한다.

```powershell
# 전체 임베딩 비교를 새로 수행할 때만 실행. 모델 다운로드와 계산이 발생한다.
$env:CHUNKING_ALLOW_HF_DOWNLOAD = "1"
python embedding_experiment/run_experiment.py

# 보존된 BGE 후보로 병합 입력을 재생성. LLM 호출 없음.
python merge_experiment/run_merge_experiment.py
python merge_experiment/analyze_merge_gold_counts.py

# v3 요청 준비만 수행. LLM 호출 없음.
python llm_ts3003c/run_experiment.py --prepare
```

LLM 실행 코드에서 `--prepare`를 제거하면 유료 API 호출과 결과 갱신이 발생할 수 있다.
기존 응답 캐시는 저장소 밖에 있으므로 재실행 시 API가 다시 호출될 수 있다.
v2 재현 코드는 `llm_ts3003c/v2/run_experiment.py`이며 해당 v2 폴더에 결과를 기록한다.
handoff 도구는 v2의 `sent_payloads.json`을 복원하거나 재생성한 뒤 사용한다.

## 외부 보관 자료

모델 캐시, 대량 중간 결과, 요청·응답 원자료, 로그는 삭제하지 않고 `remeet` 상위 폴더의
`hyungjin/chunking_method_archive_20261027/`에 보관했다. `.gitignore`로 숨기지 않으므로
재실행으로 생성된 중간 파일은 커밋 전에 다시 분리해야 한다.

보관 자료의 `moved_files.json`은 정리 이전 경로를 기록한다. 임베딩의 이전 최상위 파일과
`runs/`, `hf_cache/`는 복원 시 `embedding_experiment/` 아래에 둔다. 병합 및 LLM 원자료의 경로는 그대로다.
폴더 재배치 기록은 외부 보관 폴더의 `reorganization_20261027.json`에 있다.
기존 manifest의 경로는 실행 당시 기록이므로 현재 위치와 다를 수 있다. 수치·예측 파일은 변경하지 않았다.

이번 정리에서는 모델/API 호출을 수행하지 않았다. 원문·정답·예측에 포함된 전사문의 공개 권한은 별도 검토 대상이다.
