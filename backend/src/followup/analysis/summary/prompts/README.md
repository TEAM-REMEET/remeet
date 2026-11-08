# 분석 프롬프트

## 채워야 할 내용

요약 · 액션 아이템의 생성(extract_summary_action_item) · 검증(verify_summary) · 재수정(revise_summary) 프롬프트와 공통 블록(common)이 있다. 추천 설명 프롬프트는 해당 단계에서 추가한다.

## 구현 원칙

원문은 명령이 아닌 분석 데이터로 취급한다. 미정·근거·변경·취소 정책과 출력 스키마를 명시한다.

## 완료 기준

실행 결과(summary.json의 config.prompt_version)에 프롬프트 버전을 기록한다. 프롬프트 변경은 고정 평가 자료로 비교하고 비밀/불필요한 개인정보를 넣지 않는다.

