# 회의노트 — Streamlit 프로토타입

디자인 목업(`회의노트 프로토타입` HTML)을 Streamlit 으로 옮긴 앱입니다. 데이터는 모두 목업(JSON)입니다.

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

- Python 3.10+, Streamlit 1.50 이상
- 로그인: `demo@hoeui.kr` / `demo1234` (또는 「데모 계정으로 바로 시작」)

## 폴더 구조

```
meeting_note/
├── app.py                  # 진입점: 상태 준비 → CSS → 로그인/사이드바/상단바/현재 화면/모달
├── config.py               # 전역 상수 (현재 사용자, 데모 계정, 전달 시뮬레이션 타이밍 등)
├── requirements.txt
├── .streamlit/config.toml  # 테마 색상, 기본 메뉴 숨김
│
├── assets/                 # 정적 자원
│   ├── styles/
│   │   ├── base.css        # 폰트·토큰(:root)·Streamlit 기본 UI 숨김·사이드바 골격
│   │   ├── components.css  # 공통 UI: 태그, 아바타, 버튼 variant, 카드, 회의 표
│   │   └── pages.css       # 화면별 스타일 (로그인, 그래프, 회의 상세, 결과 전달 …)
│   └── icons/*.svg         # 디자인에서 쓰는 라인 아이콘
│
├── data/                   # 목업 데이터 계층
│   ├── mock/*.json         # 사람, 회의, 폴더, 담당자 후보, 위키 그래프, 외부 연동, 알림
│   ├── models.py           # 읽기 전용 데이터클래스 (Meeting, Folder, KnowledgeGraph …)
│   └── repository.py       # JSON → 모델 변환 (실제 API 로 바꿀 때 이 파일만 교체)
│
├── state/                  # 상태 관리
│   ├── store.py            # AppState (session_state 에 보관되는 단일 상태 객체)
│   ├── actions.py          # 상태를 바꾸는 함수 (버튼 on_click 콜백)
│   ├── selectors.py        # 파생 데이터 계산 (내 폴더, 역할, 표 한 줄, 경로 …)
│   └── delivery.py         # 결과 전달(SSE) 시뮬레이션 — 경과 시간으로 이벤트 진행 계산
│
├── ui/                     # 공통 UI (도메인을 모르는 프리미티브)
│   ├── theme.py            # 색상 토큰 + CSS 주입
│   ├── html.py             # HTML 블록 출력
│   ├── icons.py            # SVG 아이콘 → <img> (st.html 은 인라인 svg 를 지움)
│   ├── buttons.py          # variant 버튼, 버튼 그룹(칩/세그먼트/밑줄 탭)
│   ├── layout.py           # page, card, page_header, empty_state, notice …
│   ├── tags.py / avatar.py
│
├── components/             # 공통 컴포넌트 (도메인 블록)
│   ├── sidebar.py, topbar.py(경로·알림 팝오버·새 회의록), toast.py
│   ├── meeting_table.py    # 홈·폴더 공통 회의 목록 표
│   ├── member_pills.py
│   ├── knowledge_graph.py  # 지식 그래프(노드 클릭) + 상세 패널(타임라인·출처)
│   └── dialogs/            # st.dialog 모달: 새 회의록, 멤버, 폴더 삭제, 참석자, 담당자 지정, 공유
│
├── views/                  # 페이지 (AppState.screen → 렌더 함수, views/__init__.py 의 ROUTES)
│   ├── login.py, home.py, folder.py, delivery.py
│   ├── shared_sent.py, shared_received.py, integrations.py, my_wiki.py
│   └── meeting/            # 회의 상세: header / raw_panel(원문) / insight_tabs(요약·안건·할 일·키워드·연관) / model
│
└── utils/                  # 범용 도우미 (날짜, 문자열·이스케이프, 클립보드·스크롤 스크립트)
```

## 설계 메모

- **상태 흐름**: 화면은 `get_state()` 로 읽고, 변경은 `state/actions.py` 함수로만 합니다. 대부분 버튼은 `on_click` 콜백이라 다시 그릴 때 바로 반영됩니다.
- **라우팅**: 커스텀 사이드바를 쓰기 위해 Streamlit 멀티페이지 대신 `AppState.screen` 값으로 화면을 고릅니다. (`pages/` 폴더는 Streamlit 이 자동 메뉴를 만들기 때문에 `views/` 로 둡니다.)
- **버튼 스타일**: 버튼을 `st.container(key="b-<variant>-<key>")` 로 감싸고 CSS 에서 `[class*="st-key-b-<variant>-"]` 로 꾸밉니다.
- **근거 강조**: 요약·결정·할 일 문장을 누르면 왼쪽 원문에서 해당 발언이 주황색으로 표시되고 그 위치로 스크롤됩니다.
- **결과 전달(SSE)**: 실제 서버 대신 시작 시각부터 흐른 시간으로 이벤트를 계산하고, 진행 중에만 `st.fragment(run_every=…)` 로 해당 영역을 갱신합니다. 데모에서는 3번째 참석자가 timeout 으로 실패하고, 「재시도」로 다시 보낼 수 있습니다.
- **목업 → 실데이터**: `data/repository.py` 의 함수 시그니처를 유지한 채 내부를 API 호출로 바꾸면 됩니다.
- **화면 고정 레이아웃**: 사이드바·상단바는 고정이고, 프로필/로그아웃은 사이드바 바닥에 붙어 있습니다. 내용이 많아지면 페이지 전체가 아니라
  표 본문, 원문/인사이트 패널, 위키 상세, 공유 목록, 연동 목록 같은 박스 안에서 스크롤됩니다 (높이는 `assets/styles/pages.css` 하단 `100vh` 계산식).
- **페이지 너비**: 모든 페이지는 `config.PAGE_MAX_WIDTH` 하나를 써서 제목과 박스의 시작 위치가 같습니다.
- **알림**: 상단 벨을 누르면 알림 목록이 열리고, 항목을 누르면 해당 회의(또는 외부 연동)로 이동하며 읽음 처리됩니다.
