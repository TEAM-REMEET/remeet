# 사용 사례·Harness

## 채워야 할 내용

register_meeting.py, analyze_meeting.py, recommend_assignees.py, finalize_review.py와 필요한 구성원 관리 사용 사례를 작성한다.

## 구현 원칙

ports.py에는 실제 필요한 저장·모델·Job 경계만 정의한다. Harness는 병렬 실행·체크포인트·호출 제한·제한적 재시도·실패 상태를 관리한다.

## 완료 기준

권한·중복 요청·동시 수정·재시도 시 기존 데이터 보존을 검증한다. 전체 Job 재시도와 단계 재시도 상한을 조정한다.

