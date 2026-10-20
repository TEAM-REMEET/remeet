# 백엔드 구성

## 채워야 할 내용

domain → 업무 개념, application → 사용 사례, analysis → 분석 단계, adapters → 외부 기술, entrypoints → 실행 진입점을 구현한다.

## 구현 원칙

domain은 FastAPI·Neo4j·LLM SDK를 가져오지 않는다. application은 필요한 외부 기능의 경계에 의존하고 bootstrap에서 구현체를 연결한다.

## 완료 기준

API와 Worker에 업무 규칙을 중복 작성하지 않는다. settings에 설정을 모으고 비밀값은 환경/비밀 저장소로 주입한다.

