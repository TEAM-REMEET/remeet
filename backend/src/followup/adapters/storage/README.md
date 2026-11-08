# 결과 저장소

## 채워야 할 내용

application/ports.py의 SummaryStore를 구현한다. 지금은 local_files.py(로컬 파일)가 있으며, 외부 저장소를 정하면 같은 메서드의 구현체로 교체한다.

## 구현 원칙

쓰는 도중 실패해도 기존 결과가 손상되지 않게 한다. 재실행용 단계별 LLM 응답은 결과 저장에 성공하면 삭제한다.

## 완료 기준

저장소를 바꿔도 application과 analysis를 수정하지 않는다.
