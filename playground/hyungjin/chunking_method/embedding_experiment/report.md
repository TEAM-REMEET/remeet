# 경계 후보 추출 실험 결과

실행일: 2026-10-26

## 실행 개요

- 조건: 144/288 전체 조건 완료.
- 입력 20개, 정답 경계 153개. LLM 및 유료 임베딩 API 호출은 0회.
- 평가는 ±200자와 ±300자를 별도로 집계.

## 모델 실행 상태

- BAAI/bge-m3: 완전 72, 부분 0, 실패 0/72
- Gameselo/STS-multilingual-mpnet-base-v2: 완전 0, 부분 18, 실패 54/72
- jinaai/jina-embeddings-v5-text-small-text-matching: 완전 72, 부분 0, 실패 0/72
- alphabrothers/alpha-sts-v0: 완전 0, 부분 18, 실패 54/72

## ±200자

| 순위 | 모델 | 윈도우 | 오버랩 | 분할 | 방식 | 임계값 | 포함률 | 완전 포함 회의 | 후보 수 |
|---:|---|---:|---:|---|---|---:|---:|---:|---:|
| 1 | BAAI/bge-m3 | 200 | 150 | sentence | depth | 0.10 | 100.0% (153/153) | 20/20 | 1553 |
| 2 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 150 | sentence | depth | 0.05 | 100.0% (153/153) | 20/20 | 1663 |
| 3 | BAAI/bge-m3 | 200 | 150 | sentence | depth | 0.05 | 100.0% (153/153) | 20/20 | 1707 |
| 4 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 150 | sentence | depth | 0.02 | 100.0% (153/153) | 20/20 | 1722 |
| 5 | BAAI/bge-m3 | 200 | 150 | sentence | depth | 0.02 | 100.0% (153/153) | 20/20 | 1755 |
| 6 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 150 | sentence | depth | 0.10 | 99.3% (152/153) | 19/20 | 1452 |
| 7 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 100 | sentence | depth | 0.05 | 98.7% (151/153) | 18/20 | 938 |
| 8 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 100 | sentence | depth | 0.02 | 98.7% (151/153) | 18/20 | 953 |
| 9 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 100 | sentence | depth | 0.10 | 96.7% (148/153) | 15/20 | 854 |
| 10 | BAAI/bge-m3 | 200 | 100 | sentence | depth | 0.02 | 96.7% (148/153) | 15/20 | 970 |
| 11 | BAAI/bge-m3 | 200 | 100 | sentence | depth | 0.05 | 95.4% (146/153) | 13/20 | 959 |
| 12 | BAAI/bge-m3 | 200 | 100 | sentence | depth | 0.10 | 94.1% (144/153) | 12/20 | 915 |
| 13 | jinaai/jina-embeddings-v5-text-small-text-matching | 300 | 150 | sentence | depth | 0.02 | 89.5% (137/153) | 6/20 | 619 |
| 14 | BAAI/bge-m3 | 200 | 50 | sentence | depth | 0.05 | 88.9% (136/153) | 9/20 | 600 |
| 15 | BAAI/bge-m3 | 200 | 50 | sentence | depth | 0.02 | 88.9% (136/153) | 9/20 | 603 |

### 모델별 최고 조건

| 모델 | 설정 | 포함률 | 완전 포함 회의 | 후보 수 |
|---|---|---:|---:|---:|
| BAAI/bge-m3 | 200/150자 sentence depth 0.10 | 100.0% | 20/20 | 1553 |
| Gameselo/STS-multilingual-mpnet-base-v2 | 완전 실행 조건 없음 (18개 부분, 54개 실패) | — | — | — |
| jinaai/jina-embeddings-v5-text-small-text-matching | 200/150자 sentence depth 0.05 | 100.0% | 20/20 | 1663 |
| alphabrothers/alpha-sts-v0 | 완전 실행 조건 없음 (18개 부분, 54개 실패) | — | — | — |

### 공통 설정에서 모델 비교 (300자/100자/문장 보존)

| 방법·임계값 | BAAI/bge-m3 | Gameselo/STS-multilingual-mpnet-base-v2 | jinaai/jina-embeddings-v5-text-small-text-matching | alphabrothers/alpha-sts-v0 |
|---|---:|---:|---:|---:|
| depth 0.02 | 79.1%; 462 | — | 79.7%; 459 | — |
| depth 0.05 | 78.4%; 454 | — | 77.8%; 440 | — |
| depth 0.10 | 76.5%; 420 | — | 74.5%; 396 | — |
| quantile 0.02 | 11.1%; 36 | — | 10.5%; 36 | — |
| quantile 0.05 | 22.2%; 75 | — | 23.5%; 75 | — |
| quantile 0.10 | 35.9%; 140 | — | 35.9%; 140 | — |

### 후보 수 대비 포함률

파레토 우위 조건 중 포함률 상위 8개입니다. 막대 하나는 5%p입니다.

- `BAAI/bge-m3 w200/o150 sentence depth 0.10` `████████████████████` 100.0%, 후보 1553
- `jinaai/jina-embeddings-v5-text-small-text-matching w200/o150 sentence depth 0.10` `████████████████████` 99.3%, 후보 1452
- `jinaai/jina-embeddings-v5-text-small-text-matching w200/o100 sentence depth 0.05` `████████████████████` 98.7%, 후보 938
- `jinaai/jina-embeddings-v5-text-small-text-matching w200/o100 sentence depth 0.10` `███████████████████` 96.7%, 후보 854
- `jinaai/jina-embeddings-v5-text-small-text-matching w300/o150 sentence depth 0.02` `██████████████████` 89.5%, 후보 619
- `jinaai/jina-embeddings-v5-text-small-text-matching w300/o150 sentence depth 0.05` `██████████████████` 88.9%, 후보 598
- `jinaai/jina-embeddings-v5-text-small-text-matching w200/o50 sentence depth 0.05` `██████████████████` 87.6%, 후보 583
- `BAAI/bge-m3 w200/o50 sentence depth 0.10` `█████████████████` 85.6%, 후보 561

**권고:** ±200자 기준으로 BAAI/bge-m3 / 200자 / 오버랩 150자 / sentence / depth 0.10 조건이 우선입니다. 포함률 100.0%, 완전 포함 20/20, 후보 1553개입니다.

## ±300자

| 순위 | 모델 | 윈도우 | 오버랩 | 분할 | 방식 | 임계값 | 포함률 | 완전 포함 회의 | 후보 수 |
|---:|---|---:|---:|---|---|---:|---:|---:|---:|
| 1 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 100 | sentence | depth | 0.10 | 100.0% (153/153) | 20/20 | 854 |
| 2 | BAAI/bge-m3 | 200 | 100 | sentence | depth | 0.10 | 100.0% (153/153) | 20/20 | 915 |
| 3 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 100 | sentence | depth | 0.05 | 100.0% (153/153) | 20/20 | 938 |
| 4 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 100 | sentence | depth | 0.02 | 100.0% (153/153) | 20/20 | 953 |
| 5 | BAAI/bge-m3 | 200 | 100 | sentence | depth | 0.05 | 100.0% (153/153) | 20/20 | 959 |
| 6 | BAAI/bge-m3 | 200 | 100 | sentence | depth | 0.02 | 100.0% (153/153) | 20/20 | 970 |
| 7 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 150 | sentence | depth | 0.10 | 100.0% (153/153) | 20/20 | 1452 |
| 8 | BAAI/bge-m3 | 200 | 150 | sentence | depth | 0.10 | 100.0% (153/153) | 20/20 | 1553 |
| 9 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 150 | sentence | depth | 0.05 | 100.0% (153/153) | 20/20 | 1663 |
| 10 | BAAI/bge-m3 | 200 | 150 | sentence | depth | 0.05 | 100.0% (153/153) | 20/20 | 1707 |
| 11 | jinaai/jina-embeddings-v5-text-small-text-matching | 200 | 150 | sentence | depth | 0.02 | 100.0% (153/153) | 20/20 | 1722 |
| 12 | BAAI/bge-m3 | 200 | 150 | sentence | depth | 0.02 | 100.0% (153/153) | 20/20 | 1755 |
| 13 | jinaai/jina-embeddings-v5-text-small-text-matching | 300 | 150 | sentence | depth | 0.05 | 99.3% (152/153) | 19/20 | 598 |
| 14 | jinaai/jina-embeddings-v5-text-small-text-matching | 300 | 150 | sentence | depth | 0.02 | 99.3% (152/153) | 19/20 | 619 |
| 15 | BAAI/bge-m3 | 200 | 50 | sentence | depth | 0.05 | 98.7% (151/153) | 18/20 | 600 |

### 모델별 최고 조건

| 모델 | 설정 | 포함률 | 완전 포함 회의 | 후보 수 |
|---|---|---:|---:|---:|
| BAAI/bge-m3 | 200/100자 sentence depth 0.10 | 100.0% | 20/20 | 915 |
| Gameselo/STS-multilingual-mpnet-base-v2 | 완전 실행 조건 없음 (18개 부분, 54개 실패) | — | — | — |
| jinaai/jina-embeddings-v5-text-small-text-matching | 200/100자 sentence depth 0.10 | 100.0% | 20/20 | 854 |
| alphabrothers/alpha-sts-v0 | 완전 실행 조건 없음 (18개 부분, 54개 실패) | — | — | — |

### 공통 설정에서 모델 비교 (300자/100자/문장 보존)

| 방법·임계값 | BAAI/bge-m3 | Gameselo/STS-multilingual-mpnet-base-v2 | jinaai/jina-embeddings-v5-text-small-text-matching | alphabrothers/alpha-sts-v0 |
|---|---:|---:|---:|---:|
| depth 0.02 | 90.2%; 462 | — | 93.5%; 459 | — |
| depth 0.05 | 88.9%; 454 | — | 91.5%; 440 | — |
| depth 0.10 | 86.9%; 420 | — | 88.2%; 396 | — |
| quantile 0.02 | 11.8%; 36 | — | 11.8%; 36 | — |
| quantile 0.05 | 24.8%; 75 | — | 25.5%; 75 | — |
| quantile 0.10 | 41.2%; 140 | — | 40.5%; 140 | — |

### 후보 수 대비 포함률

파레토 우위 조건 중 포함률 상위 8개입니다. 막대 하나는 5%p입니다.

- `jinaai/jina-embeddings-v5-text-small-text-matching w200/o100 sentence depth 0.10` `████████████████████` 100.0%, 후보 854
- `jinaai/jina-embeddings-v5-text-small-text-matching w300/o150 sentence depth 0.05` `████████████████████` 99.3%, 후보 598
- `jinaai/jina-embeddings-v5-text-small-text-matching w200/o50 sentence depth 0.05` `████████████████████` 98.0%, 후보 583
- `BAAI/bge-m3 w200/o50 sentence depth 0.10` `███████████████████` 97.4%, 후보 561
- `jinaai/jina-embeddings-v5-text-small-text-matching w300/o100 sentence depth 0.02` `███████████████████` 93.5%, 후보 459
- `jinaai/jina-embeddings-v5-text-small-text-matching w300/o100 sentence depth 0.05` `██████████████████` 91.5%, 후보 440
- `jinaai/jina-embeddings-v5-text-small-text-matching w300/o100 sentence depth 0.10` `██████████████████` 88.2%, 후보 396
- `BAAI/bge-m3 w400/o150 sentence depth 0.02` `█████████████████` 85.0%, 후보 353

**권고:** ±300자 기준으로 jinaai/jina-embeddings-v5-text-small-text-matching / 200자 / 오버랩 100자 / sentence / depth 0.10 조건이 우선입니다. 포함률 100.0%, 완전 포함 20/20, 후보 854개입니다.

## 결과 해석

MPNet과 Alpha STS는 128토큰 입력 한도를 넘은 회의가 있어 완전 실행 조건이 없으며, 일부 짧은 윈도우 설정만 부분 실행됐습니다. 포함률 100% 미달 조건의 누락 경계와 최근접 후보 거리는 `missed_boundaries.csv`에서 확인할 수 있습니다. 모델 접근 실패·입력 한도 초과 등 실행 불가 조건은 `experiment_matrix.csv`에 사유를 기록했습니다.
임베딩 시간·모델 로드 시간·후보 계산 시간은 `per_meeting.csv`와 `aggregate.csv`에 기록했습니다. 메모리는 후보 계산 중 프로세스 RSS 표본으로 기록했으며 모델 가중치를 포함합니다.

200자·300자 윈도우 조건에서 정답 하나를 여러 후보가 포함하는 빈도는 `duplicate_boundary_analysis.md`와 CSV 두 파일에 정리했습니다.

## 겹치는 후보 구간 병합

기존 후보 중심의 ±반경 문맥 구간을 만들고 겹치는 구간을 병합했다. 포함률은 구간 합집합이므로 유지되며, 288개 조건에서 기존 포함 정답 수와 일치했다. 각 행의 윈도우 크기와 오버랩을 따로 보존한 상세 평가·후보 길이는 `merged_candidate_analysis.md`에 있다.

| 모델 | 윈도우/오버랩 | 반경 | 포함률 | 후보 수 전→후 | 병합 길이 평균/중앙/최대 | 복수 정답 후보 | 후보당 최대 정답 | 정답 없는 후보 비율 전→후 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BGE-m3 | 200/150 | ±200자 | 100.0% | 1,553→42 | 6,296/5,490/18,601자 | 31 | 9 | 77.8%→7.1% |
| Jina text-matching | 200/100 | ±300자 | 100.0% | 854→65 | 4,014/3,284/14,939자 | 35 | 12 | 67.3%→15.4% |

병합 뒤 후보 수와 정답 없는 구간 비율은 줄지만 후보 구간이 길어지고 여러 정답을 한 구간에 담는다. 오탐률은 병합 전 후보 중심 단위, 병합 후 구간 단위다.

## 한계

- 결과는 같은 20개 회의에서 설정을 비교한 것이며 독립 시험 성능이 아닙니다.
- 문장 분할은 기존의 문장부호 기반 규칙을 사용하며 약어를 별도로 판별하지 않습니다.
- 처리 시간은 로컬 CPU 실행 기준이며 모델 로드와 임베딩 시간은 per_meeting.csv에 기록합니다.

## 추가 분석: 상용 적용을 위한 BGE-m3 결과 정리

작성일: 2026-10-27. 기존 `aggregate.csv`의 완전 실행 결과를 재집계했다. 실험을 추가 실행하지 않았다.

### 라이선스와 모델 선택

실험에 사용한 Jina `jina-embeddings-v5-text-small-text-matching`의 공개 라이선스는 **CC BY-NC 4.0**이며, 상업용 사용은 별도 허가·계약이 필요하다. BGE-m3는 **MIT**로 공개되어 상업용 사용이 가능하며, 복제·배포 시 저작권과 라이선스 고지를 유지해야 한다. 확인일은 2026-10-27이다. [Jina 라이선스](https://huggingface.co/jinaai/jina-embeddings-v5-text-small-text-matching#license), [BGE-m3 모델 카드](https://huggingface.co/BAAI/bge-m3), [MIT 라이선스 조건](https://opensource.org/license/mit).

따라서 앞선 순위표와 Jina 권고는 성능 비교 기록으로 유지하되, **별도 상업용 계약 없이 적용할 후속 실험의 기준 모델은 BGE-m3로 권고한다.** 이는 현재 실험에서 전체 회의를 처리한 모델 중의 선택이며, 실행이 제한된 MPNet·Alpha보다 성능이 우수하다는 결론은 아니다.

### BGE-m3 실행 범위

- 4개 윈도우 × 3개 오버랩 × 문장 보존 × 2개 후보 방법 × 3개 임계값, 총 **72/72개 조건**을 20개 회의에서 완전 실행했다.
- depth 임계값은 0.02·0.05·0.10, quantile은 하위 2%·5%·10%다.
- 정답 경계는 153개이며 ±200자와 ±300자 포함률을 각각 평가했다.

### 주요 설정별 결과

아래 조건은 모두 BGE-m3, 문장 보존, 목표 윈도우 200자다. 후보 수는 20개 회의의 합계이며, 같은 후보 집합을 두 반경으로 평가했다.

| 목표 오버랩 | 방법 | 임계값 | 후보 수 | ±200자 포함 | ±200자 완전 포함 회의 | ±300자 포함 | ±300자 완전 포함 회의 |
|---:|---|---:|---:|---:|---:|---:|---:|
| 100자 | depth | 0.02 | 970 | 148/153 (96.7%) | 15/20 | 153/153 (100.0%) | 20/20 |
| 100자 | depth | 0.05 | 959 | 146/153 (95.4%) | 13/20 | 153/153 (100.0%) | 20/20 |
| **100자** | **depth** | **0.10** | **915** | **144/153 (94.1%)** | **12/20** | **153/153 (100.0%)** | **20/20** |
| 150자 | depth | 0.02 | 1,755 | 153/153 (100.0%) | 20/20 | 153/153 (100.0%) | 20/20 |
| 150자 | depth | 0.05 | 1,707 | 153/153 (100.0%) | 20/20 | 153/153 (100.0%) | 20/20 |
| **150자** | **depth** | **0.10** | **1,553** | **153/153 (100.0%)** | **20/20** | **153/153 (100.0%)** | **20/20** |
| 150자 | quantile | 0.10 | 512 | 103/153 (67.3%) | 1/20 | 123/153 (80.4%) | 4/20 |

quantile 행은 이번 탐색 범위에서 BGE-m3의 quantile 최고 포함률 조건이다. 후보는 적지만 ±300자에서도 30개 정답을 놓치므로, 누락 최소화 목적에서는 depth를 선택한다. 이는 하위 2%·5%·10%의 결과이며 더 넓은 분위수까지 비교한 결론은 아니다.

### 후속 LLM 실험의 기준 설정

| 항목 | 권고값 |
|---|---|
| 임베딩 모델 | `BAAI/bge-m3` |
| 윈도우 방식 | 문장 보존 |
| 목표 윈도우 크기 | 200자 |
| 목표 오버랩 | 100자 |
| 목표 이동 간격 | 100자 |
| 후보 선출 | depth ≥ 0.10 |
| depth 주변 범위 | 좌우 각각 최대 3개 검사점 |
| 후보 주변 문맥 | 좌우 각각 300자, 총 600자 |

이 설정은 **정답 153/153개 포함, 완전 포함 회의 20/20개, 후보 915개**다. 회의당 평균 후보는 45.75개다. 문장 보존 방식이므로 실제 윈도우 크기와 오버랩은 목표값과 다를 수 있다.

±200자에서 100%를 달성한 오버랩 150자 설정과 비교하면 후보를 **638개(41.1%)** 줄인다. 단, 문맥을 좌우 300자로 넓힌 조건에서의 비교다. ±200자로 줄이면 같은 915개 후보의 포함률은 94.1%로 내려가므로, 오버랩 100자 설정을 문맥 범위와 분리하여 채택하면 안 된다.

후보별 문맥을 독립적으로 전달한다고 가정한 최대 본문량은 `915 × 600 = 549,000자`다. ±200자·오버랩 150자 설정은 `1,553 × 400 = 621,200자`로, 전자는 약 11.6% 적다. 이는 본문 양끝의 잘림·문맥 중복·병합·프롬프트·토큰화를 반영하지 않은 단순 상한 비교이며 실제 API 비용은 아니다.

저장된 임베딩 시간 합계는 오버랩 100자 조건이 약 **355.7초**, 150자 조건이 약 **671.4초**다. 동일 윈도우의 임베딩은 임계값과 평가 반경 간 재사용하므로 각 행의 시간을 중복 합산하지 않는다. 해당 수치는 이번 로컬 CPU 실행의 관측값이다.

### 남은 검증과 결론

- 권고 설정의 후보 중 **68.3%**는 ±300자 안에 정답 경계가 없다. 이는 최종 오탐률이 아니라 LLM이 기각해야 할 후보 부담이다.
- 후보 문맥에 정답이 있다는 사실만 확인했다. 실제 LLM 입력에서 정답 발화 ID를 선택지로 제공하고, LLM이 이를 정확히 추출하는지는 다음 실험에서 검증해야 한다.
- 같은 20개 회의에서 선택한 설정이므로 미사용 회의에 대한 일반화 성능은 미검증이다.
- ±200자 문맥으로 제한해야 한다면 대안은 **BGE-m3 / 200자 / 오버랩 150자 / 문장 보존 / depth 0.10**이다.

**상용 적용을 고려한 후속 실험 기준은 BGE-m3 / 200자 / 오버랩 100자 / 문장 보존 / depth 0.10 / 좌우 300자 문맥으로 선정하는 것이 타당하다.** 후보 생성 기준 설정의 선정이며, 최종 회의 구조화 성능의 확정을 뜻하지 않는다.

## 후속 실험: 후보 병합과 중앙 재배치

2026-10-27에 위 BGE-m3 설정의 후보 915개를 재사용하여 비교했다. 상세 방법·회의별 결과·검증은 [병합 실험 레포트](../merge_experiment/report.md)에 기록했다. 실행 코드는 [run_merge_experiment.py](../merge_experiment/run_merge_experiment.py)다.

| 방법 | 예상 호출 수 | 정답 포함 | 기존 대비 호출 변화 |
|---|---:|---:|---:|
| 미병합 ±300자 | 915 | 153/153 (100.0%) | 기준선 |
| 범위 보존 최대 900자 | 565 | 153/153 (100.0%) | 38.3% 감소 |
| 범위 보존 최대 1,200자 | 347 | 153/153 (100.0%) | 62.1% 감소 |
| 모든 겹침 쌍의 중앙 ±300자 | 1,480 | 148/153 (96.7%) | 61.7% 증가 |
| 모든 겹침 쌍의 중앙 ±500자 | 1,480 | 153/153 (100.0%) | 61.7% 증가 |

앞뒤 및 비인접 후보까지 모든 겹침 쌍 1,481개를 계산했다. 중앙 재배치는 겹침 교집합의 중간점을 각각 생성하고 같은 좌표만 중복 제거하며, 고립 후보는 유지한다. 원래 후보에서 한 번 계산하며 새 문맥의 재귀적 병합은 하지 않는다. 이 방식은 겹침 쌍이 많아 호출이 증가했다. 연결 그룹 전체를 하나의 중심으로 줄이는 보조 실험도 수행했으나, 긴 그룹의 양끝 문맥을 잃어 포함률이 크게 낮아졌다.

**이번 실험에서는 범위 보존 최대 1,200자를 권고한다.** 원문 합집합과 선택 가능한 기존 발화 ID 전체를 유지했고, 20개 회의에서 정답 153개를 모두 포함했다. 총 본문량은 546,104자에서 353,840자로 35.2% 줄었다. LLM은 호출하지 않았으며, 실제 경계 추출 성능과 토큰 비용은 별도 검증이 필요하다.
