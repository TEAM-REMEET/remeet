# RE:MEET — Streamlit 프로토타입

회의록을 올리면 AI가 요약·결정사항·미해결 이슈·액션 아이템 초안을 만들고, 주관자가 원문 근거를 보며 검토·확정한 뒤 Slack·메일·Jira 등으로 전달하는 흐름을 **더미데이터**로 재현한 프로토타입입니다.
AI 분석은 실제로 호출하지 않고, 경과 시간에 따라 단계가 진행되도록 시뮬레이션합니다.

## 실행

```bash
uv add "streamlit>=1.66"        # 또는 pip install -r requirements.txt
cd apps/web                      # 테마 설정(.streamlit/config.toml)은 실행 위치 기준으로 읽혀요
uv run streamlit run app.py
```

> Streamlit **1.66 이상**이 필요해요. 가로 배치 컨테이너(`st.container(horizontal=True)`), 탭 상태 유지(`st.tabs(key=...)`), 입력창 아이콘 등을 써요.

| 데모 계정 | 비밀번호 | 역할 |
|---|---|---|
| `seoyeon.lee` | `remeet` | 회의 주관자 (등록·검토·확정·전달) |
| `junho.park` | `remeet` | 참석자 (열람, 내 업무 진행 상태 변경) |
| `admin` | `remeet` | 시스템 관리자 (모든 회의 열람, 구성원 정보 수정) |

화면 아래 **데모 데이터 초기화**를 누르면 처음 상태로 돌아갑니다. 데이터는 브라우저 세션 동안만 유지됩니다.

## 폴더 구조

```
remeet/
├─ app.py                  # 진입점: 페이지 설정 → 상태 초기화 → 헤더 → 라우팅 → 푸터
├─ requirements.txt
├─ .streamlit/config.toml  # 테마(화이트 + 블루)
│
├─ assets/                 # 정적 자원
│  ├─ styles.css           #   화면 디자인(색 토큰, 배지, 카드, 히어로, 화면별 스타일)
│  ├─ widgets.css          #   Streamlit 위젯(버튼·입력·탭 등)을 프로토타입 모양으로 덮어쓰기
│  └─ logo.svg
│
├─ core/                   # 앱 공통 기반
│  ├─ state.py             #   상태 관리 (st.session_state 읽기/쓰기는 여기서만)
│  ├─ router.py            #   화면 이동 (route = {page, args})
│  └─ auth.py              #   로그인·로그아웃·열람/수정 권한
│
├─ data/                   # 더미데이터와 코드값
│  ├─ constants.py         #   상태·라벨·단계 등 코드값
│  ├─ seed.py              #   구성원·계정·연동·알림·회의 시드
│  ├─ templates.py         #   회의별 분석 결과 템플릿
│  └─ sample_transcript.txt
│
├─ services/               # 화면과 무관한 업무 규칙 (순수 함수, Streamlit 미사용)
│  ├─ analysis.py          #   분석 진행 시뮬레이션, 기한 파싱, 새 회의 생성
│  ├─ rules.py             #   검토 경고, 담당자 추천 점수
│  ├─ review.py            #   승인·거절·담당자·수정·확정 + 변경 이력
│  ├─ delivery.py          #   전달 페이로드, 대상별 본문(Markdown/메시지/텍스트/Jira), 전송
│  └─ search.py            #   회의 결과 검색
│
├─ ui/                     # 공통 UI
│  ├─ theme.py             #   CSS 주입, 로고
│  ├─ components.py        #   배지·태그·알림 박스·요약 셀 등 작은 컴포넌트
│  ├─ layout.py            #   헤더·푸터·페이지 제목
│  ├─ doc_preview.py       #   전체 미리보기 / 대상별 미리보기
│  └─ landing.py           #   메인 히어로·단계 소개 섹션
│
├─ views/                  # 페이지 (각 파일의 render()가 한 화면)
│  ├─ main.py              #   메인(로그인 + 서비스 소개)
│  ├─ dashboard.py         #   대시보드 + 검색창
│  ├─ meetings.py          #   회의 목록 (전체 / 내 회의 / 주관 / 참석)
│  ├─ register.py          #   새 회의 등록
│  ├─ progress.py          #   분석 진행 (실패 시 이 화면에서 재시도)
│  ├─ review.py            #   결과 검토 (왼쪽 원문 · 오른쪽 탭)
│  ├─ review_dialogs.py    #   검토 화면 모달 모음
│  ├─ delivery.py          #   전달 대상·미리보기·전송·전달 현황
│  ├─ search.py            #   검색 결과 + 회의 흐름
│  ├─ members.py           #   구성원 (추천 데이터)
│  └─ integrations.py      #   연동 설정 (Phase 2: 상태 동기화·리마인더)
│
├─ tests/                  # python tests/smoke.py · python tests/flow.py
│
└─ utils/                  # 범용 도구
   ├─ dates.py             #   날짜 포맷, D-day (기준일 2026-10-22)
   └─ text.py              #   ID 생성, HTML 이스케이프, 문단 번호
```

의존 방향은 `views → ui / core → services → data / utils` 한 방향입니다. `services`는 Streamlit을 import하지 않으므로 실제 백엔드 API로 바꾸거나 단위 테스트하기 쉽습니다.

## 스타일 규칙 (컨테이너 key)

Streamlit은 `st.container(key="이름")`에 `st-key-이름` CSS 클래스를 붙여 줘요. 이 클래스로 모양을 정해요.

| key 접두어 | 모양 |
|---|---|
| `panel_` / `panel0_` | 흰 패널 / 안쪽 여백 없는 흰 패널 (`ui.layout.panel()`) |
| `tap_` | 영역 전체 클릭 — 안의 마지막 버튼이 투명하게 영역을 덮음 (`ui.layout.tap()`) |
| `tr_` / `trf_` | 표의 한 줄 / 빨간 배경 줄(실패) |
| `bleed_` | 화면 너비 가득 (헤더·푸터·히어로) |
| `sm_` · `line_` · `chip_` · `ultab_` · `cta_` | 작은 버튼 · 파란 테두리 버튼 · 둥근 칩 · 밑줄 탭 · 굵은 글자 링크 |
| `eq_` | 안의 `st.columns` 좌우 패널 아래 끝을 같은 높이로 맞춤 (대시보드) |
| `scroll_` · `scrollsm_` · `scrolllg_` | 내용이 길어지면 그 안에서 스크롤 (최대 420 · 300 · 560px) |

CSS는 캐시하지 않아서, `assets/*.css`를 고친 뒤 브라우저를 새로고침하면 바로 반영돼요.

## 주요 규칙 (프로토타입 기준)

- **수정 권한**: 회의 주관자만 수정할 수 있고, 한 곳이라도 외부로 **전송되기 전까지** 수정할 수 있습니다.
- **확정 후 수정**: 다시 ‘검토 대기’가 되고, 다시 확정하면 버전이 올라갑니다. 승인한 액션을 고치면 그 액션은 ‘검토 전’으로 돌아갑니다.
- **변경 이력**: 모든 수정은 변경 전·후 값과 함께 남습니다.
- **담당자 추천**: 담당자가 없는 업무만 후보 3명을 추천합니다(스킬 40% + 부서·역할 40% + 업무 여유도 20%). 자동 배정은 하지 않습니다.
- **전달**: 승인한 액션만 보냅니다. 같은 확정 버전은 대상마다 한 번만 보내고(멱등 키), 실패한 대상만 다시 보낼 수 있습니다.
- **미리보기**: 이메일·Notion·Confluence는 전체 미리보기와 같은 Markdown 문서, Slack·Teams는 목록형 메시지, 캘린더는 일정 + 설명 텍스트, Jira는 액션별 이슈 카드로 보여줍니다.

## Streamlit 특성상 달라진 점

- 오른쪽 항목(안건·결정·이슈·액션)을 누르면 왼쪽 원문이 강조되고 해당 문단으로 스크롤돼요. 원문 문장을 직접 눌러 반대로 찾는 기능은 없어요.
- 액션 아이템을 누르면 상세 모달이 열리고, 승인·거절·담당자 지정·수정을 모달 안에서 해요(Streamlit은 모달을 한 번에 하나만 띄울 수 있어요).
- 분석 진행 화면은 `st.fragment(run_every=1.5)`로 1.5초마다 자동 갱신됩니다.
- 실제 서비스로 옮길 때는 `services/analysis.py`의 시뮬레이션을 LLM 호출·작업 큐로, `core/state.py`의 세션 상태를 DB로 바꾸면 됩니다.
