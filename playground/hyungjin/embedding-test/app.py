"""Run from the repository: uv run streamlit run playground/hyungjin/embedding-test/app.py --server.address 127.0.0.1"""
import html
import json

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import pipeline as p

st.set_page_config(page_title="회의 주제 나누기", layout="wide")
p.load_environment()
st.title("회의 주제 나누기")
st.caption("전사문 → 문자 길이 기반 임베딩 → 경계 후보 → LLM 검토 → 주제별 전문")


def show_text(text):
    st.markdown(f'<div style="white-space:pre-wrap;overflow-wrap:anywhere">{html.escape(text)}</div>', unsafe_allow_html=True)


saved = p.saved_results()
with st.expander("저장한 결과 열기", expanded="result" not in st.session_state):
    if saved:
        chosen = st.selectbox("저장 결과", saved, format_func=lambda path: str(path.relative_to(p.OUTPUT_ROOT)))
        if st.button("결과 열기"):
            try:
                st.session_state.result = p.load_saved_result(chosen)
            except (ValueError, KeyError, OSError) as exc:
                st.error(f"결과를 열 수 없습니다: {exc}")
    else:
        st.info("분석한 결과가 여기에 쌓입니다.")

with st.sidebar:
    st.header("전사문 입력")
    mode = st.radio("입력 방법", ["기존 전사문", "파일 업로드", "직접 입력"])
    raw, filename = None, "meeting.txt"
    if mode == "기존 전사문":
        files = sorted(p.INPUT_DIR.glob("*.txt"))
        if files:
            file = st.selectbox("전사문", files, format_func=lambda path: path.name)
            raw, filename = file.read_bytes(), file.name
        else:
            st.info("기존 전사문 폴더에 TXT 파일이 없습니다.")
    elif mode == "파일 업로드":
        uploaded = st.file_uploader("TXT 또는 DOCX", type=["txt", "docx"])
        if uploaded:
            raw, filename = uploaded.getvalue(), uploaded.name
    else:
        text = st.text_area("전사문 텍스트", height=200)
        raw = text.encode("utf-8") if text.strip() else None
    meeting_name = st.text_input("저장할 회의 이름", value=p.Path(filename).stem)
    embedding_provider = st.selectbox("임베딩 제공자", list(p.EMBEDDING_MODELS))
    embedding_model = st.text_input("임베딩 모델", p.EMBEDDING_MODELS[embedding_provider], key=f"embed_{embedding_provider}")
    llm_provider = st.selectbox("검토 LLM 제공자", ["openai", "gemini"])
    llm_model = st.text_input("검토 LLM 모델", "gpt-4.1-mini" if llm_provider == "openai" else "gemini-2.5-flash", key=f"llm_{llm_provider}")
    base_url = st.text_input("검토 API 주소", "https://api.openai.com/v1" if llm_provider == "openai" else "https://generativelanguage.googleapis.com/v1beta/openai/", key=f"url_{llm_provider}")

blocks = None
if raw:
    try:
        source, blocks, input_meta = p.prepare_input(raw, filename)
        with st.expander("입력 확인"):
            st.write(input_meta)
            st.caption(f"정규화한 원문 {len(source):,}자 · 본문 {blocks[-1]['end_char'] - blocks[0]['start_char']:,}자")
            show_text(source)
    except ValueError as exc:
        st.error(str(exc))

with st.form("settings"):
    st.subheader("1. 문자 길이로 윈도우 만들기")
    a, b, c = st.columns(3)
    window_chars = a.number_input("윈도우 문자 수", min_value=1, value=500)
    overlap_chars = b.number_input("오버랩 문자 수", min_value=0, value=100)
    batch_size = c.number_input("임베딩 배치 크기", min_value=1, value=8)
    st.caption("발화 개수가 아닌 원문의 문자 수로 자릅니다. 오버랩은 윈도우보다 작아야 합니다.")
    if blocks and 0 <= overlap_chars < window_chars:
        body_chars = blocks[-1]["end_char"] - blocks[0]["start_char"]
        stride = window_chars - overlap_chars
        count = 1 + max(0, (body_chars - window_chars + stride - 1) // stride)
        st.caption(f"이동 간격 {stride}자 · 적용된 설정의 예상 윈도우 {count}개 (변경 설정은 실행 시 적용)")
    st.subheader("2. 경계 후보 선택")
    method = st.selectbox("선택 방법", p.METHODS, index=4)
    st.caption("threshold: 낮은 유사도 · quantile: 하위 비율 · top_k: 낮은 순 K개 · local_minima: 주변보다 낮은 지점 · depth: 양옆 최고점 대비 골짜기 깊이")
    a, b, c = st.columns(3)
    threshold = a.number_input("유사도 임계값", min_value=-1.0, max_value=1.0, value=0.5)
    quantile = b.number_input("하위 비율", min_value=0.01, max_value=0.99, value=0.2)
    top_k = c.number_input("최대 K개", min_value=1, value=5)
    a, b, c = st.columns(3)
    min_depth = a.number_input("최소 depth", min_value=0.0, value=0.05)
    depth_radius = b.number_input("depth 주변 비교 범위", min_value=1, value=3)
    min_distance_chars = c.number_input("후보 간 최소 문자 거리", min_value=0, value=400)
    st.subheader("3. 후보를 가운데에 두고 LLM 검토")
    a, b, c = st.columns(3)
    left_chars = a.number_input("왼쪽 문맥 문자 수", min_value=1, max_value=400, value=400)
    right_chars = b.number_input("오른쪽 문맥 문자 수", min_value=1, max_value=400, value=400)
    max_candidates = c.number_input("검토 후보 수 제한 (0 = 전체)", min_value=0, value=0)
    with st.expander("시스템 프롬프트 편집"):
        system_prompt = st.text_area("시스템 프롬프트", p.SYSTEM_PROMPT, height=300)
    reuse = st.checkbox("같은 입력과 설정의 저장 결과 재사용", value=True)
    device = st.selectbox("Hugging Face 실행 장치", ["자동", "cpu", "cuda"])
    st.caption("실행 버튼을 눌렀을 때만 모델을 호출합니다. API 모델 사용 시 텍스트가 해당 제공자로 전송되며 비용이 발생할 수 있습니다.")
    a, b = st.columns(2)
    candidates_only = a.form_submit_button("임베딩·후보 탐색만 실행", use_container_width=True)
    verify = b.form_submit_button("후보 탐색·LLM 검토 실행", use_container_width=True)

if candidates_only or verify:
    config = dict(meeting_name=meeting_name, embedding_provider=embedding_provider, embedding_model=embedding_model,
                  llm_provider=llm_provider, llm_model=llm_model, base_url=base_url, window_chars=window_chars,
                  overlap_chars=overlap_chars, batch_size=batch_size, method=method, threshold=threshold,
                  quantile=quantile, top_k=top_k, min_depth=min_depth, depth_radius=depth_radius,
                  min_distance_chars=min_distance_chars, left_chars=left_chars, right_chars=right_chars,
                  max_candidates=max_candidates, system_prompt=system_prompt, reuse=reuse,
                  device=None if device == "자동" else device)
    if not raw:
        st.error("전사문을 입력하세요.")
    else:
        try:
            with st.status("분석 중", expanded=True) as status:
                result = p.analyze(raw, filename, config, run_llm=verify, progress=st.write)
                st.session_state.result = result
                status.update(label="분석 종료", state="error" if result["status"] == "failed" else "complete", expanded=False)
        except (ValueError, OSError) as exc:
            st.error(str(exc))

if "result" in st.session_state:
    result = st.session_state.result
    data, meta = result["result"], result["meta"]
    st.divider()
    st.subheader(meta["title"])
    labels = {"complete": "검토 완료", "needs_review": "판단 보류 후보 있음", "partial": "일부 후보만 검토됨",
              "candidates_only": "후보 탐색 완료", "no_candidates": "경계 후보 없음", "failed": "실행 실패"}
    st.info(labels.get(result["status"], result["status"]))
    if result.get("error"):
        st.error(result["error"]["message"])
    st.caption(f"임베딩: {meta['embed_model']} · 검토: {meta.get('llm_model') or '미실행'}")
    st.caption(f"일시: {meta.get('date') or '없음'} · 참석자: {', '.join(meta.get('members', [])) or '없음'}")
    if result["analysis_scope"]["is_partial"]:
        st.warning("이 결과는 전사문 일부만 분석한 결과입니다.")
    a, b, c = st.columns(3)
    a.metric("후보", len(data["candidates"]))
    b.metric("검토", len(data["boundary_reviews"]))
    c.metric("주제", len(data["chunks"]))
    topics, review_tab, chart, export = st.tabs(["주제별 전문", "후보 검토", "유사도 그래프", "JSON 저장"])
    with topics:
        if not data["chunks"]:
            st.info("전체 후보의 검토가 끝나고 판단 보류가 없어야 주제별 전문을 생성합니다.")
        for chunk in data["chunks"]:
            st.subheader(chunk["title"])
            st.caption(f"문자 {chunk['start_char']}–{chunk['end_char']}")
            if chunk.get("reason"):
                st.caption(f"경계 근거: {chunk['reason']}")
            show_text(chunk["transcript"])
        with st.expander("원문 전체 보기"):
            show_text(result["transcript"])
    with review_tab:
        requests = data["candidate_requests"]
        if requests:
            selected = st.selectbox("검토할 후보", range(len(requests)), format_func=lambda i: f"{requests[i]['candidate_id']} · 문자 {requests[i]['candidate_char']}", key=f"candidate_{result['provenance'].get('result_path')}")
            request = requests[selected]
            st.caption(f"실제 문맥: 왼쪽 {len(request['left_context'])}자 · 오른쪽 {len(request['right_context'])}자")
            a, b = st.columns(2)
            with a:
                st.markdown("**왼쪽 문맥**")
                show_text(request["left_context"])
            with b:
                st.markdown("**오른쪽 문맥**")
                show_text(request["right_context"])
            decision = next((r for r in data["boundary_reviews"] if r["candidate_id"] == request["candidate_id"]), None)
            if decision:
                st.json(decision)
            else:
                st.caption("아직 검토하지 않은 후보입니다.")
        st.dataframe(pd.DataFrame(data["candidates"]), hide_index=True)
    with chart:
        if data["scores"]:
            frame = pd.DataFrame(data["scores"])
            fig, axes = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
            for ax, column in zip(axes, ("similarity", "depth")):
                ax.plot(frame["candidate_char"], frame[column])
                ax.set_ylabel(column)
                for row in data["candidates"]:
                    ax.axvline(row["candidate_char"], alpha=0.25, color="tomato")
            axes[-1].set_xlabel("Character position (red = candidate)")
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.caption("비교할 인접 임베딩이 없습니다.")
    with export:
        name = p.export_name(result["config"].get("meeting_name") or meta["title"], meta["embed_model"], meta.get("llm_model"))
        for label, value, suffix in (("전체 분석 JSON", result, ".json"), ("주제별 전문 JSON", p.topics_output(result), "_topics.json")):
            st.download_button(label, json.dumps(value, ensure_ascii=False, indent=2), file_name=name + suffix,
                               mime="application/json", on_click="ignore")
        st.caption(f"로컬 저장 위치: {result['provenance'].get('result_path', '')}")
        st.json(result, expanded=False)
