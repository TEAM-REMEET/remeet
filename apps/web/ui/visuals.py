"""시각화 부품: 회의 마인드맵 · 시간 흐름 띠 · 키워드 · 회의 시간 막대.

그림은 SVG/HTML로 그리고, 누를 수 있는 곳에는 투명 버튼(hotspot)을 정확히 겹쳐 올려 기존 화면 이동·근거 강조와 연결합니다.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import streamlit as st

from ui.components import html
from utils.text import esc

# ---------------------------------------------------------------- 계산

def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _clock(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def meeting_minutes(m: dict) -> int:
    return max(1, _minutes(m["end"]) - _minutes(m["start"]))


def topic_times(m: dict) -> list[dict]:
    """안건별 논의 시간(분). 발언 시각이 없어 ‘안건이 차지한 문단 수 비율 × 회의 길이’로 추정합니다."""
    d = m.get("detail")  # 분석이 끝난 회의만
    if not d or not d["topics"]:
        return []
    total = meeting_minutes(m)
    seg_n = max(len(d["segments"]), max(t["to"] for t in d["topics"]))
    out, start = [], _minutes(m["start"])
    for t in d["topics"]:
        share = (t["to"] - t["from"] + 1) / seg_n
        out.append({**t, "min": total * share, "share": share})
    acc = 0.0  # 반올림 오차가 쌓이지 않게 누적값으로 시각 계산
    for x in out:
        x["start"] = _clock(round(start + acc))
        acc += x["min"]
        x["end"] = _clock(round(start + acc))
    return out


def topic_counts(d: dict, topic: dict) -> dict:
    """안건에 딸린 결정·액션·이슈 수 (결정·이슈는 근거 문단이 안건 범위에 있는지로 판단)."""
    rng = range(topic["from"], topic["to"] + 1)
    in_rng = lambda refs: any(r in rng for r in refs)  # noqa: E731
    return {"결정": sum(1 for x in d["decisions"] if in_rng(x["refs"])),
            "액션": sum(1 for a in d["actions"] if a["review"] != "rejected" and (a["topic"] == topic["id"] or (not a.get("topic") and in_rng(a["refs"])))),
            "이슈": sum(1 for x in d["issues"] if in_rng(x.get("refs", [])))}


_PARTICLE = re.compile(r"(으로|에서|에게|까지|부터|이다|한다|하기|하고|은|는|이|가|을|를|의|에|와|과|로|도|만)$")
STOPWORDS = {"작성", "정리", "추가", "단계", "개선안", "요청", "검토", "확정", "다음", "회의", "여부", "이후", "시점", "초안", "가능",
             "기준", "진행", "변경", "있게", "있는지", "후", "시", "수", "건", "대응", "공유", "참석자", "확인", "원인", "항목", "결과",
             "적용", "순서", "최종", "비교", "방안", "개발", "설계", "안내", "문구", "정한다", "종료", "완료로", "배포", "계산", "로직",
             "선택", "직후", "단계별", "시안", "버튼", "화면", "나중에", "건너뛸", "발송", "측정", "수집"}


def keywords(rows: list[dict], limit: int = 12) -> list[tuple[str, int]]:
    """검색 색인(결정·액션·이슈·안건 문장)에서 자주 나온 단어. 조사를 떼고 흔한 업무 단어는 뺍니다."""
    cnt: Counter = Counter()
    for r in rows:
        weight = 2 if r["type"] == "topic" else 1  # 안건 제목에 나온 단어는 조금 더 무겁게
        for raw in re.split(r"[\s,.·‘’'\"()?/]+", r["text"]):
            w = _PARTICLE.sub("", raw)
            if len(w) >= 2 and w not in STOPWORDS and not w.isdigit():
                cnt[w] += weight
    return cnt.most_common(limit)


# ---------------------------------------------------------------- hotspot: 그림 위에 투명 버튼 겹치기

def hotspots(key: str, svg: str, size: tuple[int, int], spots: list[dict]) -> None:
    """spots: {"box": (x, y, w, h)  # SVG 좌표, "label", "on_click", "args"} — 버튼 위치는 SVG 크기 대비 %로 맞춥니다."""
    vw, vh = size
    css = []
    with st.container(key=f"hs_{key}"):
        html(svg)
        for i, s in enumerate(spots):
            x, y, w, h = s["box"]
            k = f"hsb_{key}_{i}"
            css.append(f".st-key-{k}{{left:{x / vw * 100:.2f}%;top:{y / vh * 100:.2f}%;width:{w / vw * 100:.2f}%!important;height:{h / vh * 100:.2f}%}}")
            st.button(s["label"], key=k, on_click=s["on_click"], args=s.get("args", ()), kwargs=s.get("kwargs", {}), help=s.get("help"))
    html(f"<style>{''.join(css)}</style>")


# ---------------------------------------------------------------- ① 회의 마인드맵 + 시간 흐름 띠 (결과 검토 · 요약 탭)

W, H = 680, 480
_CX, _CY, _RX, _RY = 340, 186, 230, 152
_ANGLES = {1: [180], 2: [180, 0], 3: [-150, -30, 90], 4: [-140, -40, 40, 140], 5: [-150, -30, 30, 90, 150], 6: [-150, -90, -30, 30, 90, 150]}


def _wrap(text: str, width: int = 7) -> list[str]:
    """원 안 제목을 두 줄로 (어절 단위)."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return [ln if len(ln) <= width + 2 else ln[:width + 1] + "…" for ln in lines]


def meeting_map(m: dict, selected_id: str | None, on_pick, n_people: int) -> None:
    """가운데 회의, 주변에 안건 원. 원 크기와 선 굵기 = 논의 시간. 원·띠를 누르면 on_pick(topic, index)."""
    d = m["detail"]
    times = topic_times(m)
    if not times:
        return
    n = len(times)
    angles = _ANGLES.get(n) or [360 * i / n - 90 for i in range(n)]
    longest = max(t["min"] for t in times)
    total = meeting_minutes(m)

    lines, nodes, labels, spots = [], [], [], []
    for i, (t, ang) in enumerate(zip(times, angles)):
        r = 30 + 30 * math.sqrt(t["min"] / longest)
        x = _CX + _RX * math.cos(math.radians(ang))
        y = _CY + _RY * math.sin(math.radians(ang))
        on = selected_id == t["id"]
        lines.append(f'<line x1="{_CX}" y1="{_CY}" x2="{x:.0f}" y2="{y:.0f}" stroke="var(--accent-line)" stroke-width="{1.5 + 4 * t["min"] / longest:.1f}"/>')
        fill, stroke, ink = ("var(--accent)", "var(--accent)", "#fff") if on else ("var(--accent-tint)", "var(--accent-line)", "var(--accent-ink)")
        title = _wrap(t["title"])
        ty = y - (14 if len(title) == 2 else 6)
        texts = "".join(f'<text x="{x:.0f}" y="{ty + j * 17:.0f}" text-anchor="middle" font-size="14" font-weight="700" fill="{ink}">{esc(ln)}</text>' for j, ln in enumerate(title))
        sub_y = ty + len(title) * 17 + 2
        nodes.append(f'<g class="mm-node"><circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>{texts}'
                     f'<text x="{x:.0f}" y="{sub_y:.0f}" text-anchor="middle" font-size="12" fill="{ink}" opacity=".85">{round(t["min"])}분 · {round(t["share"] * 100)}%</text></g>')
        c = topic_counts(d, t)
        meta = " · ".join(f"{k} {v}" for k, v in c.items() if v) or "결정·액션 없음"
        labels.append(f'<text x="{x:.0f}" y="{y + r + 17:.0f}" text-anchor="middle" font-size="12" fill="var(--muted)">{meta}</text>')
        spots.append({"box": (x - r, y - r, 2 * r, 2 * r), "label": f"안건 {i + 1} · {t['title']} 근거 보기", "on_click": on_pick, "args": (t, i)})

    # 시간 흐름 띠
    bx, bw, by, bh = 24, W - 48, 402, 34
    ribbon, ticks, acc = [], [f'<text x="{bx}" y="{by + bh + 20}" font-size="12" fill="var(--muted)">{m["start"]}</text>'], 0.0
    for i, t in enumerate(times):
        x0 = bx + bw * acc / total
        wpx = bw * t["min"] / total
        acc += t["min"]
        on = selected_id == t["id"]
        fill, ink = ("var(--accent)", "#fff") if on else (("var(--accent-tint)", "var(--accent-ink)") if i % 2 == 0 else ("#DCE7FB", "var(--accent-ink)"))
        label = t["title"] if len(t["title"]) * 13 < wpx - 12 else (f"안건 {i + 1}" if wpx > 52 else str(i + 1))
        ribbon.append(f'<rect x="{x0 + 1:.1f}" y="{by}" width="{max(wpx - 2, 2):.1f}" height="{bh}" rx="4" fill="{fill}"/>'
                      f'<text x="{x0 + wpx / 2:.1f}" y="{by + 22}" text-anchor="middle" font-size="12" font-weight="600" fill="{ink}">{esc(label)}</text>')
        anchor = "end" if i == n - 1 else "middle"
        ticks.append(f'<text x="{x0 + wpx:.1f}" y="{by + bh + 20}" text-anchor="{anchor}" font-size="12" fill="var(--muted)">{t["end"]}</text>')
        spots.append({"box": (x0, by, wpx, bh), "label": f"{t['start']}–{t['end']} {t['title']} 원문 보기", "on_click": on_pick, "args": (t, i)})

    head = _wrap(m["title"], 11)
    hy = _CY - (16 if len(head) == 2 else 6)
    center = (f'<rect x="{_CX - 88}" y="{_CY - 38}" width="176" height="76" rx="8" fill="var(--surface)" stroke="var(--line-strong)" stroke-width="1.2"/>'
              + "".join(f'<text x="{_CX}" y="{hy + j * 18}" text-anchor="middle" font-size="14" font-weight="700" fill="var(--fg)">{esc(ln)}</text>' for j, ln in enumerate(head))
              + f'<text x="{_CX}" y="{hy + len(head) * 18 + 4}" text-anchor="middle" font-size="12" fill="var(--muted)">{total}분 · 참석 {n_people}명</text>')
    svg = (f'<svg class="mm-svg" viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="안건별 논의 시간 마인드맵">'
           + "".join(lines) + center + "".join(nodes) + "".join(labels)
           + f'<text x="{bx}" y="{by - 12}" font-size="12" font-weight="600" fill="var(--fg-3)">회의 시간 흐름</text>'
           + "".join(ribbon) + "".join(ticks) + "</svg>")
    hotspots("mindmap", svg, (W, H), spots)


# ---------------------------------------------------------------- ② 키워드 (대시보드)

def keyword_cloud(words: list[tuple[str, int]], on_pick) -> None:
    """많이 나온 단어일수록 크게. 누르면 on_pick(word) — 검색 화면으로 이어집니다."""
    if not words:
        st.caption("이번 주에 정리된 회의가 아직 없어요.")
        return
    top = words[0][1]
    low = words[-1][1]
    # 큰 단어가 가운데 오도록 섞기: 작은 것 · 큰 것 · 작은 것 …
    order = sorted(range(len(words)), key=lambda i: (i % 2, i if i % 2 else -i))
    css = []
    with st.container(key="kwcloud", horizontal=True, horizontal_alignment="center", vertical_alignment="center", gap=None):
        for i in order:
            w, c = words[i]
            t = (c - low) / (top - low) if top > low else 1
            size = round(14 + 20 * t)
            color = "var(--accent-ink)" if i < 3 else "var(--fg-2)" if t > 0.3 else "var(--muted)"
            css.append(f".st-key-kw_{i} button p{{font-size:{size}px!important;color:{color}!important;font-weight:{700 if i < 3 else 500 if t > 0.3 else 400}!important}}")
            st.button(w, key=f"kw_{i}", type="tertiary", on_click=on_pick, args=(w,), help=f"{c}회 언급 · 눌러서 검색")
    html(f"<style>{''.join(css)}</style>")


# ---------------------------------------------------------------- ③ 회의 시간 막대 (대시보드)

def time_bar_html(m: dict, longest: int) -> str:
    """회의 하나의 막대. 막대 안은 안건별 구간으로 나뉘어 어디에 시간을 썼는지 보여줘요."""
    total = meeting_minutes(m)
    times = topic_times(m)
    if times:
        segs = "".join(f'<span title="{esc(t["title"])} · {round(t["min"])}분" style="flex:{t["share"]:.4f} 1 0;'
                       f'background:{"var(--accent)" if i % 2 == 0 else "#6E9BEA"}"></span>' for i, t in enumerate(times))
        top = max(times, key=lambda t: t["min"])
        note = f'가장 오래: {esc(top["title"])} {round(top["min"])}분'
    else:
        segs = '<span style="flex:1;background:var(--line)"></span>'
        note = "분석이 끝나면 안건별로 나눠 보여줘요"
    return (f'<div class="tb-row"><div class="tb-h"><span class="fw5">{esc(m["title"])}</span><span class="muted">{total}분</span></div>'
            f'<div class="tb-track"><div class="tb-bar" style="width:{total / longest * 100:.1f}%">{segs}</div></div>'
            f'<div class="tb-note">{note}</div></div>')
