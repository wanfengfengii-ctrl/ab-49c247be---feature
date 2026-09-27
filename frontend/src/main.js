// 玻璃穹顶基线网位移联合裁决 —— 前端主逻辑（无框架，原生 ES Module）

const app = document.getElementById("app");

/** @type {{draft: object|null, report: object|null, error: string|null, stale: boolean}} */
const state = { draft: null, report: null, error: null, stale: false };

// ---------------------------------------------------------------- 工具

function h(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function sqOf(v) {
  const a = Number(v[0]);
  const b = Number(v[1]);
  return a * a + b * b;
}

async function api(path, options) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await resp.json();
  if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
  return body;
}

// ---------------------------------------------------------------- 草稿编辑（input 事件就地写回，不重绘以免失焦）

app.addEventListener("input", (ev) => {
  const el = ev.target;
  if (!el.dataset.set || !state.draft) return;
  const i = Number(el.dataset.i);
  const key = el.dataset.key;
  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : v;
  };
  switch (el.dataset.set) {
    case "point":
      state.draft.points[i][key] = key === "name" ? el.value : num(el.value);
      break;
    case "cand": {
      const j = Number(el.dataset.j);
      state.draft.candidates[i][j][key === "dx" ? 0 : 1] = num(el.value);
      el.classList.toggle("invalid", !Number.isFinite(Number(el.value)));
      const sqEl = el.closest(".cand-row")?.querySelector(".cand-sq");
      if (sqEl) {
        const s2 = sqOf(state.draft.candidates[i][j]);
        sqEl.textContent = `²=${Number.isFinite(s2) ? s2 : "—"}`;
      }
      break;
    }
    case "edge":
      if (key === "u" || key === "v") {
        state.draft.edges[i].endpoints[key === "u" ? 0 : 1] = num(el.value);
      } else {
        state.draft.edges[i][key] = num(el.value);
      }
      break;
    case "tri":
      state.draft.triangles[i][Number(el.dataset.slot)] = num(el.value);
      break;
  }
  state.stale = true;
});

function pointOptions(selected) {
  return state.draft.points
    .map((p, i) =>
      `<option value="${i}"${i === selected ? " selected" : ""}>${i}：${esc(p.name)}</option>`)
    .join("");
}

function pointCardHTML(p, i) {
  const rep = state.report;
  const used = rep?.assignment?.[i];
  const cands = state.draft.candidates[i];
  const rows = cands.map((c, j) => {
    const isPicked = used && used.candidateIndex === j;
    const s2 = sqOf(c);
    let badge = "";
    if (used) {
      if (rep.feasible) {
        badge = isPicked
          ? '<span class="badge used">采用</span>'
          : '<span class="badge rejected">未采用</span>';
      } else {
        badge = isPicked
          ? '<span class="badge evidence">证据候选</span>'
          : '<span class="badge rejected">未采用</span>';
      }
    }
    return `
      <div class="cand-row${isPicked && rep.feasible ? " is-used" : ""}">
        <span class="cand-idx">#${j}</span>
        <label class="field">dx<input type="number" data-set="cand" data-i="${i}" data-j="${j}" data-key="dx" value="${esc(c[0])}"></label>
        <label class="field">dy<input type="number" data-set="cand" data-i="${i}" data-j="${j}" data-key="dy" value="${esc(c[1])}"></label>
        <span class="cand-sq">²=${Number.isFinite(s2) ? s2 : "—"}</span>
        <span>${badge}</span>
      </div>`;
  }).join("");
  const displaced = used
    ? (rep.feasible
      ? `<div class="choice-line">位移后坐标：<span class="mono">(${used.displaced[0]}, ${used.displaced[1]})</span>　位移平方：<span class="mono">${used.displacementSq}</span></div>`
      : `<div class="choice-line" style="color:#d89191">首个违约组合中该点位移后坐标：<span class="mono">(${used.displaced[0]}, ${used.displaced[1]})</span>（非采用结论，仅为证据）</div>`)
    : "";
  const cardCls = used
    ? (rep.feasible ? " chosen" : " evidence-card")
    : "";
  return `
    <div class="point-card${cardCls}">
      <div class="point-head">
        <label class="field">名称<input data-set="point" data-i="${i}" data-key="name" value="${esc(p.name)}"></label>
        <label class="field">x（整数）<input type="number" data-set="point" data-i="${i}" data-key="x" value="${esc(p.x)}"></label>
        <label class="field">y（整数）<input type="number" data-set="point" data-i="${i}" data-key="y" value="${esc(p.y)}"></label>
        <button class="small danger" data-act="del-point" data-i="${i}"
          ${state.draft.points.length <= 5 ? "disabled" : ""}>删除点</button>
      </div>
      <div class="cand-rows">
        <div class="muted" style="font-size:11px;margin-top:4px">候选整数位移（2~3 条）</div>
        ${rows}
      </div>
      ${cands.length < 3
        ? `<button class="small" data-act="add-cand" data-i="${i}" style="margin-top:6px">添加候选</button>`
        : ""}
      ${displaced}
    </div>`;
}

function edgeRowHTML(e, i) {
  const row = state.report?.edges?.[i];
  const bad = row && !row.within;
  return `
    <div class="ref-row edge ${bad ? "badrow" : ""}">
      <select data-set="edge" data-i="${i}" data-key="u">${pointOptions(e.endpoints[0])}</select>
      <select data-set="edge" data-i="${i}" data-key="v">${pointOptions(e.endpoints[1])}</select>
      <label class="field">minSq<input type="number" min="0" data-set="edge" data-i="${i}" data-key="minSq" value="${esc(e.minSq)}"></label>
      <label class="field">maxSq<input type="number" min="0" data-set="edge" data-i="${i}" data-key="maxSq" value="${esc(e.maxSq)}"></label>
      <button class="small danger" data-act="del-edge" data-i="${i}">删</button>
    </div>`;
}

function triRowHTML(t, i) {
  const row = state.report?.triangles?.[i];
  const bad = row && !row.preserved;
  return `
    <div class="ref-row ${bad ? "badrow" : ""}">
      <select data-set="tri" data-i="${i}" data-slot="0">${pointOptions(t[0])}</select>
      <select data-set="tri" data-i="${i}" data-slot="1">${pointOptions(t[1])}</select>
      <select data-set="tri" data-i="${i}" data-slot="2">${pointOptions(t[2])}</select>
      <span class="muted mono" style="font-size:11.5px">
        ${row ? `面积×2：${row.originalSignedArea2}→<b class="${row.preserved ? "tag-ok" : "tag-bad"}">${row.actualSignedArea2}</b>` : ""}
      </span>
      <span></span>
      <button class="small danger" data-act="del-tri" data-i="${i}">删</button>
    </div>`;
}

// ---------------------------------------------------------------- 结果视图

function bannerHTML() {
  if (state.error) {
    return `<div class="banner err"><h3>草稿无法裁决</h3><div>${esc(state.error)}</div></div>`;
  }
  const r = state.report;
  if (!r) return "";
  if (r.feasible) {
    return `
      <div class="banner ok">
        <h3>✅ 存在可行方案，已按三级目标取最优</h3>
        <div>候选序号向量：<span class="mono">[${r.choice.join(", ")}]</span>
          （穷举 ${r.triedCombinations}/${r.totalCombinations} 种组合）</div>
        <div class="objs">
          ① 最大位移平方 = <b>${r.objective.maxSq}</b>
          ② 位移平方和 = <b>${r.objective.sumSq}</b>
          ③ 同分时取候选序号字典序最小
        </div>
      </div>`;
  }
  const v = r.firstViolation;
  const kindName = {
    orientation: "三角面朝向",
    length: "边长平方闭区间",
    crossing: "非共端边相交",
  }[v.kind];
  return `
    <div class="banner bad">
      <h3>⛔ 无可行方案 —— 首个违约证据（候选组合字典序第一种失败组合）</h3>
      <div>穷举 ${r.triedCombinations}/${r.totalCombinations} 种组合无一满足全部硬约束，
        不存在“局部可用”的折中结论。</div>
      <div class="evidence">
        <span class="pill">${kindName}</span>
        <div>${esc(v.message)}</div>
        <div class="kv">
          <span class="k">违约时候选序号</span><span class="v mono">[${v.choice.join(", ")}]</span>
          ${evidenceKV(v)}
        </div>
      </div>
    </div>`;
}

function evidenceKV(v) {
  if (v.kind === "orientation") {
    return `
      <span class="k">三角面顶点索引</span><span class="v mono">[${v.vertices.join(", ")}]</span>
      <span class="k">原两倍有向面积</span><span class="v">${v.originalSignedArea2}</span>
      <span class="k">实际两倍有向面积</span><span class="v">${v.actualSignedArea2}（须严格同号）</span>`;
  }
  if (v.kind === "length") {
    return `
      <span class="k">允许闭区间</span><span class="v">[${v.minSq}, ${v.maxSq}]</span>
      <span class="k">实际长度平方</span><span class="v">${v.actualSq}</span>`;
  }
  return `
    <span class="k">边 A 端点索引</span><span class="v mono">[${v.endpointsA.join(", ")}]</span>
    <span class="k">边 B 端点索引</span><span class="v mono">[${v.endpointsB.join(", ")}]</span>
    <span class="k">接触方式</span><span class="v">${v.touching ? "单点相切" : "正向相交"}</span>`;
}

function edgeTableHTML() {
  const r = state.report;
  if (!r) return "";
  const rows = r.edges.map((e) => {
    const name = (idx) => r.points[idx].name;
    return `
      <tr class="${e.within ? "" : "badrow"}">
        <td>${e.edgeIndex}</td>
        <td>${esc(name(e.endpoints[0]))}–${esc(name(e.endpoints[1]))}</td>
        <td>${e.minSq}</td>
        <td>${e.maxSq}</td>
        <td><b>${e.actualSq}</b></td>
        <td class="${e.within ? "tag-ok" : "tag-bad"}">${e.within ? "区间内 ✓" : "越界 ✗"}</td>
      </tr>`;
  }).join("");
  return `
    <div class="panel">
      <h2>各边实际长度平方证据 <span class="count">闭区间判定</span></h2>
      ${r.feasible ? "" : '<p class="muted" style="font-size:12px;margin:0 0 8px">下表为首个违约组合下的边长证据（非采用结论）。</p>'}
      <table class="data">
        <thead><tr><th>#</th><th>边</th><th>minSq</th><th>maxSq</th><th>实际²</th><th>判定</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function triTableHTML() {
  const r = state.report;
  if (!r) return "";
  const rows = r.triangles.map((t) => {
    const names = t.vertices.map((i) => esc(r.points[i].name)).join("、");
    return `
      <tr class="${t.preserved ? "" : "badrow"}">
        <td>${t.triangleIndex + 1}</td>
        <td>${names}</td>
        <td>${t.originalSignedArea2}</td>
        <td><b>${t.actualSignedArea2}</b></td>
        <td class="${t.preserved ? "tag-ok" : "tag-bad"}">
          ${t.actualSignedArea2 === 0 ? "退化共线 ✗" : t.preserved ? "同向 ✓" : "翻转 ✗"}
        </td>
      </tr>`;
  }).join("");
  return `
    <div class="panel">
      <h2>各三角面有向面积证据 <span class="count">两倍有向面积须与原基线严格同号</span></h2>
      ${r.feasible ? "" : '<p class="muted" style="font-size:12px;margin:0 0 8px">下表为首个违约组合下的朝向证据（非采用结论）。</p>'}
      <table class="data">
        <thead><tr><th>#</th><th>顶点</th><th>原面积×2</th><th>实际面积×2</th><th>朝向</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------- SVG 图

function computeProjection(r) {
  const all = r.points.map((p) => [p.x, p.y]);
  (r.movedPoints || []).forEach((p) => all.push(p));
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  all.forEach(([x, y]) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  });
  const W = 720, H = 520, M = 46;
  const spanX = Math.max(1, maxX - minX), spanY = Math.max(1, maxY - minY);
  const k = Math.min((W - 2 * M) / spanX, (H - 2 * M) / spanY);
  const ox = (W - k * spanX) / 2 - k * minX;
  const oy = (H - k * spanY) / 2 + k * maxY;
  return { W, H, pt: ([x, y]) => [ox + k * x, oy - k * y] };
}

function svgHTML() {
  const r = state.report;
  if (!r) return "";
  const { W, H, pt } = computeProjection(r);
  const orig = r.points.map((p) => [p.x, p.y]);
  const moved = r.movedPoints;
  const v = r.firstViolation;

  const poly = (pts, close) =>
    pts.map((p, idx) =>
      `${idx === 0 ? "M" : "L"}${pt(p)[0].toFixed(1)},${pt(p)[1].toFixed(1)}`).join(" ")
    + (close ? " Z" : "");

  const trisSvg = r.triangles.map((t) => {
    const dOrig = poly(t.vertices.map((i) => orig[i]), true);
    const dMoved = poly(t.vertices.map((i) => moved[i]), true);
    const bad = !t.preserved;
    return `
      <path d="${dOrig}" fill="none" stroke="#46557a" stroke-width="1" stroke-dasharray="4 4"/>
      <path d="${dMoved}" fill="${bad ? "rgba(248,113,113,0.16)" : "rgba(91,157,255,0.13)"}"
            stroke="${bad ? "#f87171" : "#5b9dff"}" stroke-width="1.4"/>`;
  }).join("");

  const edgesSvg = r.edges.map((e) => {
    const [a, b] = e.endpoints;
    const p1 = pt(moved[a]), p2 = pt(moved[b]);
    const conflict = v?.kind === "crossing"
      && (v.edgeA === e.edgeIndex || v.edgeB === e.edgeIndex);
    const lenBad = v?.kind === "length" && v.edgeIndex === e.edgeIndex;
    const color = conflict || lenBad || !e.within ? "#f87171" : "#9fc0ff";
    const width = conflict || lenBad ? 3 : 1.8;
    return `<line x1="${p1[0].toFixed(1)}" y1="${p1[1].toFixed(1)}"
      x2="${p2[0].toFixed(1)}" y2="${p2[1].toFixed(1)}"
      stroke="${color}" stroke-width="${width}"/>`;
  }).join("");

  const arrowsSvg = orig.map((o, i) => {
    const m = moved[i];
    if (o[0] === m[0] && o[1] === m[1]) return "";
    const [x1, y1] = pt(o), [x2, y2] = pt(m);
    return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"
      stroke="#fbbf24" stroke-width="1.2" stroke-dasharray="3 3" marker-end="url(#arrow)"/>`;
  }).join("");

  const ptsSvg = r.points.map((p, i) => {
    const [x, y] = pt(moved[i]);
    const [x0, y0] = pt(orig[i]);
    return `
      <circle cx="${x0.toFixed(1)}" cy="${y0.toFixed(1)}" r="2.6" fill="#46557a"/>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.2" fill="#dbe8ff" stroke="#0b101c" stroke-width="1.2"/>
      <text x="${(x + 8).toFixed(1)}" y="${(y - 7).toFixed(1)}" fill="#e6ebf5" font-size="12">${esc(p.name)}</text>`;
  }).join("");

  return `
    <div class="panel">
      <h2>复测基线网几何证据图</h2>
      <div id="svg-wrap">
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="位移后基线网">
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3"
              orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L6,3 L0,6 Z" fill="#fbbf24"/>
            </marker>
          </defs>
          ${trisSvg}${edgesSvg}${arrowsSvg}${ptsSvg}
        </svg>
      </div>
      <div class="legend">
        <span><i style="background:#5b9dff"></i>位移后三角面</span>
        <span><i style="border-top:1px dashed #46557a"></i>原基线</span>
        <span><i style="border-top:2px dashed #fbbf24"></i>位移向量</span>
        <span><i style="background:#f87171"></i>违约边/面</span>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- 渲染

function render() {
  const d = state.draft;
  if (!d) return;

  const left = h("<div></div>");
  left.innerHTML = `
    <div class="panel">
      <div class="toolbar">
        <button class="primary" data-act="submit">提交裁决</button>
        <button data-act="load-sample">载入内置示例</button>
        <button data-act="add-point" ${d.points.length >= 8 ? "disabled" : ""}>添加基准点</button>
        <span class="muted" style="align-self:center;font-size:12px">
          基准点 ${d.points.length} 个（要求 5~8），每点 2~3 条候选整数位移
        </span>
      </div>
      ${bannerHTML()}
      ${state.report && state.stale
        ? '<p class="muted" style="font-size:12px;color:var(--warn)">⚠ 草稿在上次裁决后已修改，以下证据可能过期，请重新提交裁决。</p>'
        : ""}
      ${state.report && !state.stale
        ? (state.report.feasible
          ? `<p class="muted" style="font-size:12px;margin:4px 0 10px">
               绿色「采用」为该点唯一选定的候选，灰色「未采用」为其余候选；
               可修改下方草稿后重新提交裁决。</p>`
          : `<p class="muted" style="font-size:12px;margin:4px 0 10px;color:#d89191">
               无可行方案：红色「证据候选」仅标识首个违约组合在各点取到的候选，
               <b>不是采用结论</b>；灰色为该组合未取到的候选。</p>`)
        : ""}
      ${d.points.map((p, i) => pointCardHTML(p, i)).join("")}
    </div>

    <div class="panel">
      <h2>登记三角面 <span class="count">按点索引选择三个顶点，朝向须与原基线一致</span></h2>
      ${d.triangles.map((t, i) => triRowHTML(t, i)).join("")}
      <button class="small" data-act="add-tri">添加三角面</button>
    </div>

    <div class="panel">
      <h2>登记边与长度平方闭区间</h2>
      ${d.edges.map((e, i) => edgeRowHTML(e, i)).join("")}
      <button class="small" data-act="add-edge">添加边</button>
    </div>`;

  const right = h("<div></div>");
  right.innerHTML = `${svgHTML()}${edgeTableHTML()}${triTableHTML()}`;

  app.innerHTML = "";
  app.append(left, right);
}

// ---------------------------------------------------------------- 动作

app.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn || !state.draft) return;
  const d = state.draft;
  const i = Number(btn.dataset.i);
  switch (btn.dataset.act) {
    case "load-sample":
      state.draft = await api("/api/sample");
      state.report = null;
      state.error = null;
      state.stale = false;
      render();
      break;
    case "add-point":
      if (d.points.length < 8) {
        d.points.push({ name: `P${d.points.length + 1}`, x: 0, y: 0 });
        d.candidates.push([[0, 0], [1, 0]]);
        state.stale = true;
        render();
      }
      break;
    case "del-point": {
      d.points.splice(i, 1);
      d.candidates.splice(i, 1);
      d.edges = d.edges
        .map((e) => ({
          ...e,
          endpoints: e.endpoints.map((x) => (x === i ? -1 : x > i ? x - 1 : x)),
        }))
        .filter((e) => e.endpoints[0] >= 0 && e.endpoints[1] >= 0
          && e.endpoints[0] !== e.endpoints[1]);
      d.triangles = d.triangles
        .map((t) => t.map((x) => (x === i ? -1 : x > i ? x - 1 : x)))
        .filter((t) => t.every((x) => x >= 0) && new Set(t).size === 3);
      state.report = null;
      render();
      break;
    }
    case "add-cand":
      if (d.candidates[i].length < 3) {
        d.candidates[i].push([0, 0]);
        state.stale = true;
        render();
      }
      break;
    case "add-edge":
      d.edges.push({
        endpoints: [0, Math.min(1, d.points.length - 1)],
        minSq: 0,
        maxSq: 1000,
      });
      state.stale = true;
      render();
      break;
    case "del-edge":
      d.edges.splice(i, 1);
      state.stale = true;
      render();
      break;
    case "add-tri":
      d.triangles.push([
        0,
        Math.min(1, d.points.length - 1),
        Math.min(2, d.points.length - 1),
      ]);
      state.stale = true;
      render();
      break;
    case "del-tri":
      d.triangles.splice(i, 1);
      state.stale = true;
      render();
      break;
    case "submit": {
      state.error = null;
      state.report = null;
      btn.disabled = true;
      btn.textContent = "裁决中…";
      try {
        state.report = await api("/api/adjudicate", {
          method: "POST",
          body: JSON.stringify(d),
        });
        state.stale = false;
      } catch (e) {
        state.error = e.message;
      } finally {
        render();
      }
      break;
    }
  }
});

// ---------------------------------------------------------------- 启动

(async function init() {
  try {
    state.draft = await api("/api/sample");
  } catch (e) {
    state.draft = { points: [], triangles: [], edges: [], candidates: [] };
    state.error = `无法连接裁决服务：${e.message}`;
  }
  render();
})();
