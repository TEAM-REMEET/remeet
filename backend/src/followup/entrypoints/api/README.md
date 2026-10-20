# FastAPI API·인증·SSE

## 채워야 할 내용

main.py, dependencies.py와 routes를 작성한다. 서버에서 인증과 ADMIN/VIEWER 권한을 검사한다.

## 구현 원칙

Pydantic 요청/응답 검증, 일관된 오류, 장시간 작업 접수, 분석 상태 조회와 SSE를 제공한다.

## 완료 기준

서버 메모리가 유일한 작업 상태가 되지 않는다. 연결 종료·만료 후 DB 상태로 복원하고 VIEWER 변경 요청을 거절한다.

