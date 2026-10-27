# 실험 기록

실험을 했다면 **성공/실패와 상관없이** 표에 한 줄 추가하세요. 실패한 실험도 발표의 "실패 사례 분석" 재료가 됩니다.
자세한 내용이 필요하면 `TEMPLATE.md`를 복사해서 `playground/YYYY-MM-DD_이름_주제.md`로 만들고 아래 표에 링크합니다.

| 날짜 | 담당 | 실험 | 바꾼 변수 | 결과 (지표) | 한 줄 결론 | 상세 |
|---|---|---|---|---|---|---|
| 예) 10-20 | 예시 | API 호출 | Gemini vs GPT | Acc 0.71 / 0.78 | GPT가 문서 요약에 유리함 | (링크) |
| 10-26 | 형진 | 회의 경계 후보 추출: 모델·슬라이딩 윈도우 비교 | 모델 4종, 윈도우 200/300/400/500자, 오버랩 50/100/150자, 고정/문장 보존, depth/quantile, 임계값 0.02/0.05/0.10 | 20회의·LLM 참조 경계 153개; 288조건 중 144조건 완전 실행; BGE·Jina 각각 72조건 완료 | 후보 포함률과 후보 수를 함께 비교하여 후속 설정 선정 | [보고서](hyungjin/chunking_method/embedding_experiment/report.md), [전체 집계](hyungjin/chunking_method/embedding_experiment/aggregate.csv) |
| 10-26 | 형진 | BGE 후보 주변 정답 포함 범위 선정 | BGE-m3, 200자/100자, 문장 보존, depth ≥ 0.10; 평가 반경 ±200/±300자 | 후보 915개; ±200자 144/153(94.1%), ±300자 153/153(100%), 완전 포함 회의 20/20 | 후속 문맥을 ±300자로 선택; 포함률 100%는 최종 경계 정확도가 아님 | [BGE 결과](hyungjin/chunking_method/embedding_experiment/report.md) |
| 10-27 정리 | 형진 | 길이 상한 없는 후보 구간 병합 탐색 | 겹치거나 맞닿는 후보 문맥의 전체 합집합 | 분석 288조합에서 기존 포함 정답 수 유지; BGE 200/150·depth 0.10·±200자 예시: 1,553→42묶음, 최대 18,601자 | 묶음 수는 줄지만 지나치게 긴 문맥이 생겨 길이 제한 병합을 추가 비교 | [초기 분석](hyungjin/chunking_method/merge_experiment/uncapped_analysis/merged_candidate_analysis.md) |
| 10-27 | 형진 | 범위 보존 병합 상한 비교 |미병합/최대 900자/최대 1,200자/겹치는 쌍의 중앙 재배치/연결된 전체 그룹 중앙 재배치(보조) | 요청 예정 묶음 915/565/347, 정답 포함 모두 153/153; 1,200자에서 묶음 62.1%·총 문맥 문자 35.2% 감소 | 비교 조건 중 1,200자를 LLM 입력 병합 상한으로 제안; 이 단계의 실제 LLM 호출은 0회 | [병합 보고서](hyungjin/chunking_method/merge_experiment/report.md), [집계](hyungjin/chunking_method/merge_experiment/aggregate.csv) |
| 10-27 | 형진 | 묶음당 복수 정답 분석 | 실제 요청 범위와 선택 가능한 발화 ID 기준 집계 | 1,200자 병합 347묶음 중 정답 0/1/2/3/4개 묶음은 178/142/20/6/1개; 복수 정답 27묶음(7.8%), 최대 4개 | 하나의 요청 묶음을 하나의 주제로 가정할 수 없음 | [분포·회의 목록](hyungjin/chunking_method/merge_experiment/gold_count_report.md), [복수 정답 사례](hyungjin/chunking_method/merge_experiment/multi_gold_cases.csv) |
| 10-27 | 형진 | TS3003c LLM 경계 검증 v1 | gpt-6-luna(low), 후보 주변 경계 선택지; 미병합/900/1,200자 | ±3발화 R 63.6/63.6/63.6%, F1 58.3/58.3/66.7%; 요청 실패 0/2/1개 | 후보 주변 정답 포함에도 실제 누락 발생; 병합 조건은 부분 완료라 직접 우열 비교 제한 | [v1 결과](hyungjin/chunking_method/llm_ts3003c/v1_boundary_options/report.md) |
| 10-27 | 형진 | TS3003c LLM 경계 검증 v2 | 같은 모델, 예상 경계 위치 고정 O/X; 미병합/900/1,200자 | ±3발화 R 36.4/27.3/54.5%, F1 47.1/40.0/60.0%; 모든 요청 완료; 고정 위치의 ±3 재현율 상한 8/11 | 위치 보정 없는 O/X 판정으로는 정답 경계 복원에 한계 | [v2 결과](hyungjin/chunking_method/llm_ts3003c/v2/report.md) |
| 10-27 | 형진 | TS3003c LLM 경계 검증 v3 | 같은 모델, O/X 및 예상 위치 ±5발화 내 경계 선택; 미병합/900/1,200자 | 정확 일치 R 18.2/27.3/18.2%, F1 25.0/33.3/23.5%; ±3발화 R 27.3/45.5/27.3%; 요청 실패 1/0/1개; 탐색 범위에 정답 9/11 포함 | 위치 보정을 허용해도 탐색 범위·판정 누락·검증 실패가 남음 | [v3 결과](hyungjin/chunking_method/llm_ts3003c/v3/report.md) |
| 10-27 | 형진 | 단순 1,200자 분할과 입력 묶음 수 비교 | TS3003c 본문 12,159자; 오버랩 없는 고정 길이 분할 vs 후보 범위 보존 병합 | 산술상 고정 분할 11청크 vs 1,200자 병합 16묶음 | 후보 검증 구조의 추가 이득을 입증할 필요; 단순 분할의 추출·요약 품질을 검증한 실험은 아님 | [최종 보고서 §6](hyungjin/chunking_method/final_report.md), [데이터 규모](hyungjin/chunking_method/embedding_experiment/dataset_manifest.csv) |
| 10-27 | 형진 | 의미 청킹 실험 종료 및 도입 결정 | 경계 확정을 위한 추가 LLM 검증 단계의 품질·처리 부담 검토 | v1~v3에서도 경계 누락; 보고서상 버전별 누적 요청 지연 약 497~733초; 후속 액션 추출·요약 성능 향상은 미검증 | LLM 경계 검증 도입 철회; [BGE 결과](hyungjin/chunking_method/embedding_experiment/report.md)에서 아이디어를 얻어 임베딩 기반 임시 의미 청킹 방식 채택 방향으로 전환 | [최종 마무리 보고서](hyungjin/chunking_method/final_report.md), [실험별 파일 안내](hyungjin/chunking_method/README.md) |

