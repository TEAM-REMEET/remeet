"""메인 페이지의 히어로 비주얼과 단계별 소개 섹션 (정적 HTML)."""
from __future__ import annotations

from ui.components import html


def _join(*parts: str) -> str:
    # Streamlit Markdown이 들여쓰기를 코드 블록으로 읽지 않도록 한 줄로 이어 붙입니다.
    return "".join(p.strip() for p in parts)


def hero_left() -> None:
    html(_join(
        '<div class="hero">',
        '<p class="eyebrow">회의 후속 업무 관리</p>',
        '<h1>회의가 끝나면,<br>할 일은 이미 정리돼 있습니다</h1>',
        '<p class="lead">회의록을 올리면 요약·결정사항·액션 아이템을 원문 근거와 함께 정리하고, 담당자 후보까지 추천합니다. 확정한 결과는 쓰던 협업 도구로 보냅니다.</p>',
        '<div class="hv">',
        '<div class="hv-card hv-doc"><div class="hv-h">📄 온보딩개선_1021.hwpx<span>원문</span></div>',
        '<p><b>#17</b>최동욱: 인증 실패 관련 CS 문의가 꽤 많았어요.</p>',
        '<p class="hl"><b>#18</b>이서연: 실패했을 때 안내 문구부터 손보면 좋겠네요.</p>',
        '<p class="hl"><b>#19</b>최동욱: 실제 문의 유형은 제가 정리해 드릴게요.</p>',
        '<p><b>#25</b>박준호: 로그 수집 항목은 2주 안에 추가할게요.</p></div>',
        '<div class="hv-card hv-sum"><div class="hv-h">결정사항<span>AI 초안</span></div>',
        '<p>1. 관심사 선택 단계에 ‘나중에 하기’ 추가</p><p>2. 웰컴 메일 발송 시점 A/B 테스트</p><p>3. 적용 순서는 다음 회의에서 확정</p></div>',
        '<div class="hv-card hv-act"><div class="hv-h">액션 아이템<span>검토 3/7</span></div>',
        '<div class="hv-row"><i class="ok"></i>이탈 원인 분석 리포트<em>오세훈 · 10.28</em></div>',
        '<div class="hv-row"><i class="ok"></i>‘나중에 하기’ 시안 작성<em>김민지 · 10.22</em></div>',
        '<div class="hv-row"><i></i>안내 문구 개선안 정리<em class="rec">추천 최동욱 92점</em></div></div>',
        '<div class="hv-chip c-ok" style="right:2%;top:170px">✓ Slack #온보딩-tf 전달 완료</div>',
        '<div class="hv-chip c-accent" style="right:8%;top:320px">🔗 Jira 이슈 6건 생성</div>',
        '<div class="hv-chip c-wait" style="left:0;top:315px">⚠ 기한 미정 2건 확인 필요</div>',
        '</div></div>',
    ))


FEATURES = [
    {
        "step": "01", "title": "회의록만 올리면 준비 끝",
        "desc": "파일을 끌어다 놓거나 텍스트를 붙여넣고, 제목·일시·참석자만 입력하세요. 형식과 용량은 올리는 순간 확인해 드려요.",
        "points": [".txt · .md · .docx · .hwpx 지원", "조직도에서 참석자 선택", "참고 자료 함께 보관"],
        "visual": _join(
            '<div class="fv"><div class="fv-h">새 회의 등록<span>1 / 4 단계</span></div>',
            '<div class="fv-drop"><b>회의록 파일을 끌어다 놓으세요</b><br><span class="small muted">.txt · .md · .docx · .hwpx</span></div>',
            '<div class="fv-line"><span class="tag edit">HWPX</span><span>온보딩개선_1021.hwpx · 1.2MB</span><em class="ok">확인 완료</em></div>',
            '<div class="fv-line"><span class="muted">회의 제목</span><strong>4분기 신규 회원 온보딩 개선 회의</strong></div>',
            '<div class="fv-line"><span class="muted">참석자</span>이서연 · 박준호 · 김민지 · 최동욱 · 정하은 · 오세훈</div></div>',
        ),
    },
    {
        "step": "02", "title": "주제별로 나눠 정리하고, 원문과 대조해 검증해요",
        "desc": "회의록을 주제 단위로 나눈 뒤 요약과 액션 아이템을 따로 뽑고, 근거가 원문에 실제로 있는지 하나씩 확인합니다.",
        "points": ["진행 상황을 실시간으로 표시", "근거가 부족한 항목은 ‘추정’으로 표시", "문제가 생기면 그 단계만 다시 시도"],
        "visual": _join(
            '<div class="fv"><div class="fv-h">분석 진행 현황<span class="c-accent">실시간</span></div>',
            '<div><b style="font-size:30px;color:var(--accent-ink)">64%</b> <span class="small muted">추출한 결과를 원문과 대조해 검증하고 있어요</span></div>',
            '<div class="fv-bar"><span style="width:64%"></span></div>',
            '<div class="fv-line">업로드 · 전처리 · 주제 분석 · 추출<em class="ok">완료</em></div>',
            '<div class="fv-line">검증<em class="run">진행 중</em></div>',
            '<div class="fv-line">담당자 추천 · 리뷰 준비<em class="n">대기</em></div></div>',
        ),
    },
    {
        "step": "03", "title": "원문을 옆에 두고 검토하세요",
        "desc": "항목을 누르면 근거 문단이 바로 강조돼요. 담당자가 없는 업무에는 스킬·부서·업무량을 따져 후보 3명을 추천합니다.",
        "points": ["승인·거절·수정 이력 기록", "담당자·기한은 원문에 없으면 비워 둠", "AI 추천은 제안일 뿐, 자동 배정 없음"],
        "visual": _join(
            '<div class="fv"><div class="fv-h">액션 아이템<span>검토 3/7</span></div>',
            '<div class="fv-line"><span class="c-ok">●</span>이탈 원인 분석 리포트 <span class="small muted">오세훈 · 10.28 (수)</span><em class="ok">승인</em></div>',
            '<div class="fv-line" style="background:var(--accent-tint)"><span class="muted">○</span>안내 문구 개선안 정리 <span class="small c-err">담당자 미정</span><em class="n">검토 전</em></div>',
            '<div class="quote" style="margin-top:10px"><p><b>#18</b>실패했을 때 안내 문구부터 손보면 좋겠네요.</p><p><b>#19</b>문의 유형은 제가 정리해 드릴게요.</p></div>',
            '<div class="fv-line"><strong>추천 1위 최동욱</strong> <span class="small muted">CS운영팀 · 스킬 100 · 부서 100 · 여유 60</span><em class="ok">92점</em></div></div>',
        ),
    },
    {
        "step": "04", "title": "확정한 결과를 쓰던 도구로 보내요",
        "desc": "Slack, 이메일, 캘린더, Jira, Notion 중 보낼 곳을 고르고 미리보기를 확인한 뒤 승인하면 전송됩니다.",
        "points": ["대상별 형식에 맞춘 미리보기", "같은 버전은 한 번만 전송", "실패한 대상만 다시 보내기"],
        "visual": _join(
            '<div class="fv"><div class="fv-h">전달 대상<span>버전 1</span></div>',
            '<div class="fv-line"><strong>Slack</strong><span class="small muted">#온보딩-tf 채널에 게시</span><em class="ok">전송 완료</em></div>',
            '<div class="fv-line"><strong>이메일</strong><span class="small muted">참석자 6명에게 발송</span><em class="ok">전송 완료</em></div>',
            '<div class="fv-line"><strong>캘린더</strong><span class="small muted">10.28 (수) 14:00 후속 회의</span><em class="ok">전송 완료</em></div>',
            '<div class="fv-line"><strong>Jira</strong><span class="small muted">ONB 프로젝트 · 이슈 6건</span><em class="run">전송 중</em></div>',
            '<div class="fv-line"><strong>Notion</strong><span class="small muted">팀 위키 / 회의록</span><em class="ok">전송 완료</em></div></div>',
        ),
    },
]


def feature_sections() -> None:
    for i, f in enumerate(FEATURES):
        cls = "feat" + (" rev alt" if i % 2 else "")
        points = "".join(f"<li>{p}</li>" for p in f["points"])
        html(_join(
            f'<section class="{cls}"><div class="feat-in">',
            f'<div class="feat-t"><p class="feat-step">STEP {f["step"]}</p><h2>{f["title"]}</h2><p class="d">{f["desc"]}</p><ul>{points}</ul></div>',
            f'<div>{f["visual"]}</div>',
            '</div></section>',
        ))


def help_band() -> None:
    html(_join(
        '<div class="help-band"><div><h3>도움이 필요하신가요?</h3>',
        '<p class="small muted" style="margin:0">평일 09:00–18:00 · help@remeet.example · 도입은 조직 관리자에게 SSO 연결을 요청하세요.</p></div>',
        '<div class="small"><span class="tag">회의록 작성 가이드</span> <span class="tag">지원 파일 형식</span> <span class="tag">외부 도구 연결</span></div></div>',
    ))
