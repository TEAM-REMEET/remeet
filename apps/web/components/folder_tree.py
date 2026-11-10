"""폴더 위키 「그래프」 탭: 주제 트리 (GET /api/projects/{id}/graph 데이터를 계층으로 다시 엮어 왼→오로 그림).

계층 = 폴더 → 주제 → 안건(회의 날짜순) → 주요 논의(소주제, 안건 안에서 1. 2. 3.) → 결정 · 할 일. 미해결은 소주제 구분이 없어 「미해결 이슈」 아래.
누를 때마다 한 단씩 펼치고, 다시 누르면 그 아래까지 접음.
vis-network 는 assets/vendor 에 넣어 둔 파일을 그대로 넣음 (인터넷 없이도 뜨게 · 9.1.9, Apache-2.0 OR MIT).
데이터 글자는 textContent · 캔버스로만 넣음 (회의록 문장에 HTML 이 섞여 있어도 실행되지 않게).
"""

from __future__ import annotations

import json

import streamlit.components.v1 as components

from functools import lru_cache
from pathlib import Path

_VIS = Path(__file__).parent.parent / "assets" / "vendor" / "vis-network.min.js"


@lru_cache(maxsize=1)
def _vis_js() -> str:
    return _VIS.read_text(encoding="utf-8")


_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#FFFFFF;color:#1A1A1A;font-family:"Noto Sans KR",-apple-system,"Apple SD Gothic Neo",sans-serif;font-size:14px}
#app{display:flex;flex-direction:column;height:100%;border:1px solid #E2E4E8;border-radius:6px;overflow:hidden}
.top{display:flex;gap:10px;align-items:center;padding:10px 12px;border-bottom:1px solid #E2E4E8}
.legend{display:flex;gap:6px;flex-wrap:wrap}
.chip.lg{border-radius:99px;padding:4px 12px;font-size:12.5px}
.top button{margin-left:auto;border:1px solid #DDE0E4;background:#fff;border-radius:6px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer;color:#3B4048}
button:focus-visible{outline:2px solid #F37321;outline-offset:2px}
.main{flex:1;display:flex;min-height:0}
#net{flex:1;min-width:0;background:#FFFFFF}
aside{width:320px;flex:none;border-left:1px solid #E2E4E8;overflow-y:auto;padding:20px 18px;background:#F4F5F7}
aside h3{margin:10px 0 16px;font-size:17px;font-weight:800;line-height:1.45}
.chip{display:inline-block;border-radius:3px;padding:2px 8px;font-size:12.5px;font-weight:700}
.f{margin-bottom:16px}
.f b{display:block;font-size:12.5px;color:#5F646D;font-weight:700;margin-bottom:4px}
.f p{margin:0;line-height:1.6}
.f ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px}
.f li{line-height:1.6}
.path{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:13px}
.path span.sep{color:#A3A8B1}
.empty{color:#80858E;line-height:1.6;margin:0}
.empty + .empty{margin-top:8px}
@media (max-width:760px){aside{display:none}}
</style></head><body><div id="app">
<div class="top"><div id="legend" class="legend"></div><button id="all" type="button"></button></div>
<div class="main"><div id="net"></div><aside id="panel" aria-live="polite"></aside></div>
</div>
<script>__VIS__</script>
<script>
const G = __DATA__, ROOT = __ROOT__;
// 종류별 [진한 색(테두리 · 글자), 옅은 배경]
const COLOR = {root: ["#2B2D33", "#2B2D33"], topic: ["#E8467C", "#FDE7EF"], agenda: ["#6C63D9", "#EEEDFC"], sub: ["#7A7320", "#F5F3DD"],
               dec: ["#2F538F", "#EAF0FA"], open: ["#C2410C", "#FFF1E7"], todo: ["#1F7A4D", "#E6F4EC"]};
const PICK = "#F37321";  // 고른 칸 테두리
const FACE = "Noto Sans KR, -apple-system, sans-serif";
const KIND = {root: "폴더", topic: "주제", agenda: "안건", sub: "주요 논의", dec: "결정", open: "미해결", todo: "할 일"};

// ---- 데이터 → 계층 (주제 → 안건 → 주요 논의(소주제) → 결정 · 할 일)
//   결정: 안건 decisions[] 문장 = 그 소주제 논의 중 decision_tag '결정' 인 문장 (적재가 같은 문장을 두 곳에 넣음) → 같은 문장인 소주제에
//   할 일: 소주제 -[:PRODUCED]-> 할 일
//   미해결: 소주제 구분 없이 안건에만 저장됨 → 「미해결 이슈」 칸 아래 (못 맞춘 결정도 여기)
const byId = new Map(G.nodes.map(n => [n.id, n]));
const outE = new Map(G.nodes.map(n => [n.id, []])), inE = new Map(G.nodes.map(n => [n.id, []]));
G.edges.forEach(e => { outE.get(e.from).push(e); inE.get(e.to).push(e); });
const field = (n, name) => { const f = n.fields.find(f => f.name === name); return f ? f.value : null; };
const list = v => v == null ? [] : [].concat(v);
const dateOf = a => { const m = /\((\d{4}-\d{2}-\d{2})\)$/.exec(field(a, "회의") || ""); return m ? m[1] : ""; };
const agendaById = new Map(), subById = new Map(), leafById = new Map();
const topics = G.nodes.filter(n => n.label === "Topic").map(t => {
  const topic = {node: t};
  topic.ags = inE.get(t.id).filter(e => e.type === "ABOUT" && byId.get(e.from).label === "Agenda").map(e => byId.get(e.from))
    .sort((a, b) => dateOf(a).localeCompare(dateOf(b)) || (a.order || 0) - (b.order || 0)).map(a => {
      const g = {node: a, date: dateOf(a), meeting: field(a, "회의") || ""};
      const decs = list(field(a, "결정사항")), used = new Set();
      g.subs = outE.get(a.id).filter(e => e.type === "HAS_DISCUSSION").map(e => byId.get(e.to))
        .sort((x, y) => (x.order || 0) - (y.order || 0)).map((d, i) => {
          const sub = {id: d.id, node: d, label: (i + 1) + ". " + d.caption, leaves: []};  // 안건 안에서 1, 2, 3
          decs.forEach((x, i) => {
            if (!used.has(i) && (d.lines || []).some(l => l.decision_tag === "결정" && l.text === x)) {
              used.add(i); sub.leaves.push({id: a.id + ":d" + i, kind: "dec", text: x});
            }
          });
          outE.get(d.id).filter(x => x.type === "PRODUCED").forEach(x => {
            const ai = byId.get(x.to);
            sub.leaves.push({id: a.id + ":" + ai.id, kind: "todo", text: ai.caption, node: ai,
                             owners: inE.get(ai.id).filter(y => y.type === "ASSIGNED_TO").map(y => byId.get(y.from).caption)});
          });
          return sub;
        });
      const common = decs.map((x, i) => used.has(i) ? null : {id: a.id + ":d" + i, kind: "dec", text: x}).filter(Boolean)
        .concat(list(field(a, "미해결 이슈")).map((x, i) => ({id: a.id + ":u" + i, kind: "open", text: x})));
      if (common.length) g.subs.push({id: a.id + ":common", node: null, label: "미해결 이슈", leaves: common});
      g.subs.forEach(sb => { subById.set(sb.id, {sb, g, t: topic}); sb.leaves.forEach(l => leafById.set(l.id, {l, sb, g, t: topic})); });
      agendaById.set(a.id, {g, t: topic});
      return g;
    });
  const leaves = topic.ags.flatMap(g => g.subs.flatMap(sb => sb.leaves));
  const count = k => leaves.filter(l => l.kind === k).length;
  topic.last = topic.ags.length ? topic.ags[topic.ags.length - 1].date : "";
  topic.counts = {meet: new Set(topic.ags.map(g => g.meeting)).size, dec: count("dec"), open: count("open"), todo: count("todo")};
  return topic;
}).filter(t => t.ags.length)
  .sort((a, b) => (a.node.caption === "기타") - (b.node.caption === "기타") || b.last.localeCompare(a.last));  // 기타는 맨 뒤 · 최근 논의 순
const topicById = new Map(topics.map(t => [t.node.id, t]));

// ---- 그리기
const short = (s, n) => s.length > n ? s.slice(0, n - 1) + "…" : s;
const md = d => d ? d.slice(5).replace("-", ".") : "";
const esc = x => String(x).replace(/</g, "＜");  // 라벨은 html 모드 (<b>, <i>) · 데이터 속 < 가 태그로 읽히지 않게
let opened = new Set(), selected = null;
// 모양: 폴더 = 진회색 상자 · 주제 = 분홍 알약 (펼치면 채움) · 안건 · 주요 논의 = 테두리 상자 · 잎 = 테두리 없는 옅은 상자 (모두 같은 너비)
function box(id, kind, label, extra) {
  const [deep, soft] = COLOR[kind], on = id === selected, isOpen = opened.has(id);
  let bg = soft, border = soft, width = 1.5, text = "#1A1A1A", bold = deep;
  if (kind === "root") { text = "#FFFFFF"; bold = "#FFFFFF"; }
  if (kind === "topic" || kind === "agenda" || kind === "sub") { border = deep; bold = "#1A1A1A"; }
  if (kind === "topic" && isOpen) { bg = deep; bold = "#FFFFFF"; }
  if ((kind === "agenda" || kind === "sub") && isOpen) width = 2.5;  // 펼친 안건 · 주요 논의
  if (on && !(kind === "topic" && isOpen)) { border = PICK; width = 2.5; }
  return Object.assign({id, label, color: {background: bg, border, highlight: {background: bg, border: kind === "topic" && isOpen ? deep : PICK},
    hover: {background: bg, border: kind === "root" ? deep : PICK}}, borderWidth: width,
    font: {color: text, bold: {color: bold}, align: kind === "root" || kind === "topic" ? "center" : "left"},
    shapeProperties: {borderRadius: kind === "topic" ? 20 : 4}}, extra || {});
}
// 폴더 칸은 직접 그림: vis 여러 줄 라벨은 줄 간격을 못 바꿔서 (줄 사이 0px)
const ROOT_LH = 14, ROOT_GAP = 6;  // 글자 줄 높이 · 두 줄 사이 간격(px)
function rootShape({ctx, x, y}) {
  const lines = rootLabel(ROOT);
  ctx.font = "bold 14px " + FACE;
  const w = Math.max(...lines.map(l => ctx.measureText(l).width)) + 32;
  const h = lines.length * ROOT_LH + (lines.length - 1) * ROOT_GAP + 28;
  return {nodeDimensions: {width: w, height: h}, drawNode() {
    ctx.fillStyle = COLOR.root[0];
    ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 4); ctx.fill();
    ctx.fillStyle = "#FFFFFF"; ctx.font = "bold 14px " + FACE; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    lines.forEach((l, i) => ctx.fillText(l, x, y - h / 2 + 14 + ROOT_LH / 2 + i * (ROOT_LH + ROOT_GAP)));
  }};
}
// 안건 칸도 직접 그림 (회의 줄과 안건 이름 사이 간격 때문에) · 색 · 고른/펼친 테두리는 box() 와 같은 규칙
const AG_GAP = 4;  // 회의 줄 ↔ 안건 이름 간격(px)
function wrapWords(ctx, text, max) {  // 단어 사이에서만 줄바꿈
  const out = [];
  text.split(/\s+/).filter(Boolean).forEach(word => {
    const last = out.length ? out[out.length - 1] + " " + word : word;
    if (out.length && ctx.measureText(last).width <= max) out[out.length - 1] = last; else out.push(word);
  });
  return out.length ? out : [text];
}
function agendaShape(id, meetLine, title) {
  return ({ctx, x, y, state}) => {
    const PX = 14, PY = 9, MIN = 210, MAX = 230, LH1 = 13, LH2 = 17;
    ctx.font = "bold 14px " + FACE;
    const lines = wrapWords(ctx, title, MAX - 2 * PX), tw = Math.max(...lines.map(l => ctx.measureText(l).width));
    ctx.font = "11.5px " + FACE;
    const w = Math.min(Math.max(Math.max(tw, ctx.measureText(meetLine).width) + 2 * PX, MIN), MAX);
    const h = 2 * PY + LH1 + AG_GAP + lines.length * LH2;
    return {nodeDimensions: {width: w, height: h}, drawNode() {
      const [deep, soft] = COLOR.agenda, on = id === selected || state.selected;
      ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 4);
      ctx.fillStyle = soft; ctx.fill();
      ctx.lineWidth = on || opened.has(id) ? 2.5 : 1.5; ctx.strokeStyle = on || state.hover ? PICK : deep; ctx.stroke();
      const left = x - w / 2 + PX, top = y - h / 2 + PY;
      ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.font = "11.5px " + FACE; ctx.fillStyle = "#5F646D"; ctx.fillText(meetLine, left, top);
      ctx.font = "bold 14px " + FACE; ctx.fillStyle = "#1A1A1A";
      lines.forEach((l, i) => ctx.fillText(l, left, top + LH1 + AG_GAP + i * LH2));
    }};
  };
}
// 폴더 이름: 길면 두 줄 · 두 줄 너비가 가장 비슷해지는 띄어쓰기 자리에서 나눔 (단어 안에서는 안 끊음)
//   예) Real Reaction 신제품 / TV 리모컨 개발 프로젝트
function rootLabel(name) {
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = "bold 14px " + FACE;
  const w = t => ctx.measureText(t).width, words = name.split(/\s+/).filter(Boolean);
  let best = [name], bestW = w(name);
  if (bestW > 150) for (let i = 1; i < words.length; i++) {
    const lines = [words.slice(0, i).join(" "), words.slice(i).join(" ")], m = Math.max(w(lines[0]), w(lines[1]));
    if (m < bestW) { best = lines; bestW = m; }
  }
  return best;
}
const W = (a, b) => ({widthConstraint: {minimum: a, maximum: b === undefined ? a : b}});
function build() {
  const N = [], E = [];
  N.push({id: "root", level: 0, shape: "custom", ctxRenderer: rootShape, label: undefined});
  topics.forEach(t => {
    N.push(box(t.node.id, "topic", "<b>" + esc(t.node.caption) + "</b>", Object.assign({level: 1, margin: {top: 12, bottom: 12, left: 18, right: 18}}, W(190, 220))));
    E.push({from: "root", to: t.node.id});
    if (!opened.has(t.node.id)) return;
    t.ags.forEach(g => {
      const meet = g.meeting.replace(/ \(\d{4}-\d{2}-\d{2}\)$/, "");
      N.push({id: g.node.id, level: 2, shape: "custom", ctxRenderer: agendaShape(g.node.id, short((g.date ? md(g.date) + " " : "") + meet, 24), g.node.caption)});
      E.push({from: t.node.id, to: g.node.id});
      if (!opened.has(g.node.id)) return;
      g.subs.forEach(sb => {
        N.push(box(sb.id, "sub", "<b>" + esc(short(sb.label, 18)) + "</b>", Object.assign({level: 3, margin: {top: 9, bottom: 9, left: 14, right: 14}}, W(180))));
        E.push({from: g.node.id, to: sb.id});
        if (!opened.has(sb.id)) return;
        sb.leaves.forEach(l => {
          N.push(box(l.id, l.kind, "<b>" + KIND[l.kind] + "</b>   " + esc(short(l.text, 16)), Object.assign({level: 4, margin: {top: 10, bottom: 10, left: 14, right: 14}}, W(260))));
          E.push({from: sb.id, to: l.id});
        });
      });
    });
  });
  return {N, E};
}
const nodes = new vis.DataSet(), edges = new vis.DataSet();
const net = new vis.Network(document.getElementById("net"), {nodes, edges}, {
  layout: {hierarchical: {direction: "LR", sortMethod: "directed", levelSeparation: 280, nodeSpacing: 70, treeSpacing: 80}},
  physics: false,
  nodes: {shape: "box", font: {size: 13, face: FACE, color: "#1A1A1A", multi: "html",
                               bold: {size: 14, face: FACE, mod: "bold"}, ital: {size: 11.5, face: FACE, color: "#5F646D", mod: ""},}},
  edges: {color: {color: "#C9CCD2", highlight: "#A3A8B1", hover: "#A3A8B1"}, width: 1.8, arrows: {to: false},
          smooth: {type: "cubicBezier", forceDirection: "horizontal", roundness: .5}},
  interaction: {hover: true, dragNodes: false}
});
function draw(fit) {
  const {N, E} = build();
  nodes.clear(); edges.clear(); nodes.add(N); edges.add(E);
  document.getElementById("all").textContent = opened.size ? "모두 접기" : "모두 펼치기";
  if (fit) net.once("afterDrawing", () => net.fit({animation: {duration: 400}}));  // 직접 그린 칸(폴더 · 안건)은 그려진 뒤에야 크기를 앎
}
// 아래 단 id (접을 때 그 아래 펼침도 같이 닫음)
function below(id) {
  const t = topicById.get(id), a = agendaById.get(id);
  if (t) return t.ags.flatMap(g => [g.node.id, ...g.subs.map(sb => sb.id)]);
  if (a) return a.g.subs.map(sb => sb.id);
  return [];
}
// 누르면 한 단씩: 주제 → 안건 → 주요 논의 → 결정 · 미해결 · 할 일
net.on("click", ev => {
  const id = ev.nodes[0];
  if (!id) return;
  const toggles = topicById.has(id) || agendaById.has(id) || (subById.has(id) && subById.get(id).sb.leaves.length);
  if (toggles) {
    if (opened.has(id)) { opened.delete(id); below(id).forEach(x => opened.delete(x)); }
    else opened.add(id);
  }
  selected = id; draw(toggles); panel();
});
document.getElementById("all").onclick = () => {
  if (opened.size) opened.clear();
  else topics.forEach(t => [t.node.id, ...below(t.node.id)].forEach(x => opened.add(x)));
  draw(true); panel();
};

// ---- 오른쪽 패널
function el(tag, cls, text) { const x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; }
function chip(kind, text) { const c = el("span", "chip", text || KIND[kind]); c.style.background = COLOR[kind][1]; c.style.color = COLOR[kind][0]; return c; }
function kv(p, name, value) {
  const items = list(value).filter(Boolean);
  if (!items.length) return;
  const box = el("div", "f"); box.appendChild(el("b", null, name));
  if (Array.isArray(value)) { const ul = el("ul"); items.forEach(v => ul.appendChild(el("li", null, v))); box.appendChild(ul); }
  else box.appendChild(el("p", null, value));
  p.appendChild(box);
}
function crumbs(p, t, g, sb) {
  const box = el("div", "f"); box.appendChild(el("b", null, "어디서 나왔나"));
  const row = el("div", "path");
  [chip("topic", t.node.caption), chip("agenda", (g.date ? md(g.date) + " " : "") + g.node.caption)].concat(sb ? [chip("sub", sb.label)] : [])
    .forEach((c, i) => { if (i) row.appendChild(el("span", "sep", "›")); row.appendChild(c); });
  box.appendChild(row); p.appendChild(box);
}
const lineText = d => (d && d.lines || []).map(x => (x.tag ? "[" + x.tag + "] " : "") + x.text);
function panel() {
  const p = document.getElementById("panel"); p.textContent = "";
  const t = topicById.get(selected), a = agendaById.get(selected), s = subById.get(selected), lf = leafById.get(selected);
  if (t) {
    p.appendChild(chip("topic")); p.appendChild(el("h3", null, t.node.caption));
    const c = t.counts;
    kv(p, "요약", "회의 " + c.meet + " · 결정 " + c.dec + " · 미해결 " + c.open + " · 할 일 " + c.todo);
    kv(p, "이 주제를 다룬 안건", t.ags.map(g => (g.date ? md(g.date) + " " : "") + g.node.caption));
  } else if (a) {
    p.appendChild(chip("agenda")); p.appendChild(el("h3", null, a.g.node.caption));
    kv(p, "회의", a.g.meeting);
    ["논의 배경", "결정사항", "미해결 이슈", "다음 회의 후보"].forEach(name => kv(p, name, field(a.g.node, name)));
    crumbs(p, a.t, a.g);
  } else if (s) {
    p.appendChild(chip("sub")); p.appendChild(el("h3", null, s.sb.label));
    if (s.sb.node) kv(p, "논의", lineText(s.sb.node));
    else p.appendChild(el("p", "empty", "미해결 이슈는 소주제 구분 없이 안건에만 기록돼서 이 칸에 모았어요."));
    kv(p, "회의", s.g.meeting);
    crumbs(p, s.t, s.g);
  } else if (lf) {
    p.appendChild(chip(lf.l.kind)); p.appendChild(el("h3", null, lf.l.text));
    if (lf.l.node) { kv(p, "담당", lf.l.owners.join(", ") || "아직 없음"); kv(p, "마감일", field(lf.l.node, "마감일")); kv(p, "상태", field(lf.l.node, "상태")); }
    kv(p, "회의", lf.g.meeting);
    crumbs(p, lf.t, lf.g, lf.sb);
    if (lf.sb.node) kv(p, "근거 문장", lineText(lf.sb.node));
  } else {
    p.appendChild(el("p", "empty", "주제 → 안건 → 주요 논의 순서로 누를 때마다 한 단씩 펼쳐지고, 다시 누르면 그 아래까지 접혀요."));
    p.appendChild(el("p", "empty", "아무 칸이나 누르면 내용이 여기에 보여요."));
  }
}
const lg = document.getElementById("legend");
["topic", "agenda", "sub", "dec", "open", "todo"].forEach(k => {
  const c = chip(k); c.className = "chip lg"; lg.appendChild(c);
});
draw(true); panel();
</script></body></html>"""


def folder_tree(data: dict, *, root: str, height: int = 680) -> None:
    """data: GET /api/projects/{id}/graph 결과 {nodes: [{id, label, kind, caption, fields, lines?, order?}], edges: [{from, to, type}]}.
    root: 맨 왼쪽에 둘 폴더 이름."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")  # 문장 속 </script> 가 태그를 닫지 않게
    html = (_TEMPLATE.replace("__VIS__", _vis_js())
            .replace("__ROOT__", json.dumps(root, ensure_ascii=False).replace("</", "<\\/")).replace("__DATA__", payload))
    components.html(html, height=height)
