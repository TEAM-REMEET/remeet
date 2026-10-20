# 분석 단계

## 채워야 할 내용

preprocessing.py, topic_chunking.py, summary.py, actions.py, recommendation.py, schemas.py를 작성한다.

## 구현 원칙

Summary와 Action은 같은 청크를 독립적으로 읽고 각 경로에서 병합 후 최종 검증한다. 모든 단계에 입력/출력과 근거 식별자를 유지한다.

## 완료 기준

형식 검사와 의미/근거 검증을 구분한다. 모델 호출 실패·유효하지 않은 출력·부분 결과를 Harness가 판별할 수 있게 반환한다.

