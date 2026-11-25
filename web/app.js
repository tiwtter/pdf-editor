// หน้าเว็บของ PDF Editor — ส่วน UI ล้วน ๆ ตรรกะแก้ PDF อยู่ใน pdf_core.py (รันใน worker.js)
"use strict";
const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const BUNDLED_FONTS = ["Sarabun-Regular.ttf", "Sarabun-Bold.ttf"];

// ── สถานะ ───────────────────────────────────────────────────────────
const st = {
  ready: false, doc: null, name: "", page: 0, zoom: 1.5, mode: "text",
  undo: 0, dirty: false, busy: 0,
  // โหมดข้อความ
  spans: [], sel: null, cur: null,           // cur: ดัชนีในรายการ (-1 = ทั้งกรอบ)
  // โหมดเส้น
  segs: [], lineSel: [], lineDrag: null, newLine: null,
  drag: null, userFonts: [],
};

// ── คุยกับ worker ───────────────────────────────────────────────────
let worker, msgId = 0;
const pending = new Map();
function call(cmd, ...args) {
  return new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    const transfer = args.filter((a) => a instanceof ArrayBuffer);
    worker.postMessage({ id, cmd, args }, transfer);
  });
}
function startWorker() {
  worker = new Worker("worker.js");
  worker.onmessage = (ev) => {
    const d = ev.data;
    if (d.type === "progress") return loadProgress(d.text, d.frac);
    const p = pending.get(d.id);
    pending.delete(d.id);
    d.ok ? p.resolve(d.result) : p.reject(new Error(d.error));
  };
  worker.onerror = (e) => loadFailed(e.message || "worker error");
}

// ทำงานที่แก้ไฟล์/ใช้เวลา: กันกดซ้ำ แสดงสถานะ แจ้งข้อผิดพลาด
async function task(label, fn) {
  st.busy++;
  document.body.classList.add("busy");
  if (label) status(label + "…");
  try {
    return await fn();
  } catch (e) {
    status(e.message, true);
    console.error(e);
    return undefined;
  } finally {
    if (--st.busy === 0) document.body.classList.remove("busy");
    updateButtons();
  }
}

function status(text, err = false) {
  $("status").textContent = text || "";
  $("status").classList.toggle("err", !!err);
}

// ── โหลดโปรแกรมครั้งแรก ─────────────────────────────────────────────
function loadProgress(text, frac) {
  $("loadtext").textContent = text;
  $("loadbar").style.width = Math.round((frac || 0) * 100) + "%";
  status(text);
}
function loadFailed(msg) {
  $("loading").classList.add("err");
  $("loadtext").textContent = "เปิดโปรแกรมไม่สำเร็จ: " + msg;
  status("เปิดโปรแกรมไม่สำเร็จ: " + msg, true);
}

async function boot() {
  // Service Worker เก็บไฟล์ทั้งหมดไว้ → ครั้งต่อไปเปิดได้แม้ไม่มีเน็ต
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    try {
      await navigator.serviceWorker.register("sw.js");
      if (!navigator.serviceWorker.controller) {
        // ครั้งแรก: รอให้ SW คุมหน้านี้ก่อน ไฟล์ที่ดาวน์โหลดต่อจากนี้จะได้ถูกเก็บไว้
        await Promise.race([
          new Promise((r) => navigator.serviceWorker.addEventListener("controllerchange", r, { once: true })),
          new Promise((r) => setTimeout(r, 4000)),
        ]);
      }
    } catch (e) {
      console.warn("service worker", e);
    }
  }
  startWorker();
  try {
    await call("init");
  } catch (e) {
    return loadFailed(e.message);
  }
  st.ready = true;
  $("loading").hidden = true;
  await restoreFonts();
  status(st.pendingFile ? "" : "พร้อมใช้งาน — เปิดไฟล์ PDF เพื่อเริ่ม");
  if (st.pendingFile) openFile(st.pendingFile);
}

// ── เปิด / บันทึก ───────────────────────────────────────────────────
async function openFile(file) {
  if (!file) return;
  if (st.dirty && !confirm("ไฟล์ที่แก้ยังไม่ได้บันทึก เปิดไฟล์ใหม่เลยไหม?")) return;
  if (!st.ready) {
    st.pendingFile = file;
    status(`จะเปิด ${file.name} เมื่อโปรแกรมพร้อม…`);
    return;
  }
  st.pendingFile = null;
  await task("กำลังเปิด " + file.name, async () => {
    const buf = await file.arrayBuffer();
    const info = await call("open", buf, file.name);
    st.doc = info;
    st.name = file.name.replace(/\.pdf$/i, "");
    st.page = 0;
    st.undo = info.undo;
    st.dirty = false;
    $("empty").hidden = true;
    $("pagewrap").hidden = false;
    fitZoom();
    await refresh();
    status(`เปิด ${file.name} แล้ว (${info.pages.length} หน้า)`);
  });
}

async function save() {
  await task("กำลังบันทึก", async () => {
    const bytes = await call("save");
    const blob = new Blob([bytes], { type: "application/pdf" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = st.name + "-edited.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    st.dirty = false;
    status(`ดาวน์โหลด ${a.download} แล้ว (${(bytes.length / 1024).toFixed(0)} KB)`);
  });
}

async function undo() {
  if (!st.doc || !st.undo) return;
  await task("กำลังย้อนกลับ", async () => {
    const r = await call("undo");
    st.undo = r.undo;
    st.dirty = true;
    clearTextSel();
    st.lineSel = [];
    await refresh();
    status(r.ok ? "ย้อนกลับแล้ว" : "");
  });
}

// ── แสดงหน้า ────────────────────────────────────────────────────────
const pageInfo = () => st.doc.pages[st.page];

function fitZoom() {
  const vw = $("viewer").clientWidth - 40;
  const pw = pageInfo().w;
  st.zoom = Math.max(0.5, Math.min(2, Math.floor((vw / pw) * 4) / 4));
}

async function refresh() {
  if (!st.doc) return;
  const p = pageInfo();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const img = await call("render", st.page, st.zoom * dpr);
  const cv = $("canvas");
  cv.width = img.w;
  cv.height = img.h;
  cv.getContext("2d").putImageData(new ImageData(img.data, img.w, img.h), 0, 0);
  const wrap = $("pagewrap");
  wrap.style.width = p.w * st.zoom + "px";
  wrap.style.height = p.h * st.zoom + "px";
  $("overlay").setAttribute("viewBox", `0 0 ${p.w} ${p.h}`);
  if (st.mode === "line") st.segs = await call("segments", st.page);
  $("pageinfo").textContent = `หน้า ${st.page + 1} / ${st.doc.pages.length}`;
  $("zoominfo").textContent = Math.round(st.zoom * 100) + "%";
  drawOverlay();
  updateButtons();
}

async function goto(n) {
  if (!st.doc || n < 0 || n >= st.doc.pages.length) return;
  st.page = n;
  clearTextSel();
  st.lineSel = [];
  await task("", refresh);
  $("viewer").scrollTop = 0;
}

async function setZoom(z) {
  if (!st.doc) return;
  st.zoom = Math.max(0.25, Math.min(4, z));
  await task("", refresh);
}

// ── พิกัด ───────────────────────────────────────────────────────────
// view = พิกัดตามที่เห็น (pt), page = พิกัดจริงของหน้า (ก่อนหมุน)
function evPt(e) {
  const r = $("overlay").getBoundingClientRect();
  const p = pageInfo();
  return [((e.clientX - r.left) / r.width) * p.w, ((e.clientY - r.top) / r.height) * p.h];
}
const mul = (m, x, y) => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
const toPage = (x, y) => mul(pageInfo().dm, x, y);
const toView = (x, y) => mul(pageInfo().m, x, y);
const segPts = (s, shift = 0) => {
  const pos = s.pos + shift;
  return s.o === "h" ? [[s.a, pos], [s.b, pos]] : [[pos, s.a], [pos, s.b]];
};

// ── วาดทับบนหน้า ────────────────────────────────────────────────────
function svg(tag, attrs, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  parent.appendChild(el);
  return el;
}
function drawOverlay() {
  const ov = $("overlay");
  ov.replaceChildren();
  if (!st.doc) return;
  const px = 1 / st.zoom; // 1 พิกเซลบนจอ เป็นหน่วย pt
  if (st.mode === "text") {
    if (st.sel) {
      const [x0, y0, x1, y1] = st.sel;
      svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: "none", stroke: "#e11",
        "stroke-width": 2 * px, "stroke-dasharray": `${4 * px} ${2 * px}` }, ov);
    }
    st.spans.forEach((s, i) => {
      const [x0, y0, x1, y1] = s.bbox;
      const cur = i === st.cur;
      svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: cur ? "rgba(0,102,204,.08)" : "none",
        stroke: cur ? "#06c" : "#9bd", "stroke-width": (cur ? 2 : 1) * px }, ov);
    });
  } else {
    const sel = new Set(st.lineSel);
    const shift = st.lineDrag ? st.lineDrag.d : 0;
    st.segs.forEach((s, i) => {
      const picked = sel.has(i);
      const [p1, p2] = segPts(s, picked ? shift : 0).map(([x, y]) => toView(x, y));
      svg("line", { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1], stroke: picked ? "#e11" : "#39f",
        "stroke-width": (picked ? 3 : 1) * px, "stroke-linecap": "round" }, ov);
    });
    if (st.newLine) {
      const [a, b] = st.newLine;
      svg("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: "#e11", "stroke-width": 2 * px }, ov);
    }
  }
}

// ── เมาส์ / นิ้ว ────────────────────────────────────────────────────
function onDown(e) {
  if (!st.doc || st.busy || e.button > 0) return;
  $("overlay").setPointerCapture(e.pointerId);
  const pt = evPt(e);
  st.drag = { start: pt, sx: e.clientX, sy: e.clientY, moved: false };
  if (st.mode === "line" && !$("addline").checked) pressLine(pt);
}
function onMove(e) {
  if (!st.drag) return;
  const d = st.drag;
  if (Math.abs(e.clientX - d.sx) >= 4 || Math.abs(e.clientY - d.sy) >= 4) d.moved = true;
  const [x0, y0] = d.start;
  let [x1, y1] = evPt(e);
  if (st.mode === "text") {
    if (!d.moved) return;
    st.spans = [];
    st.cur = null;
    st.sel = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
  } else if ($("addline").checked) {
    if (Math.abs(x1 - x0) >= Math.abs(y1 - y0)) y1 = y0; else x1 = x0; // บังคับแนวนอน/แนวตั้ง
    st.newLine = [[x0, y0], [x1, y1]];
  } else if (st.lineDrag && st.lineSel.length) {
    const [px, py] = toPage(x1, y1);
    const s = st.segs[st.lineSel[0]];
    st.lineDrag.d = s.o === "h" ? py - st.lineDrag.start[1] : px - st.lineDrag.start[0];
  }
  drawOverlay();
}
async function onUp(e) {
  const d = st.drag;
  if (!d) return;
  st.drag = null;
  const [x0, y0] = d.start;
  const [x1, y1] = evPt(e);
  if (st.mode === "text") return releaseText(d.moved, x0, y0, x1, y1);
  if ($("addline").checked) {
    const nl = st.newLine;
    st.newLine = null;
    drawOverlay();
    if (nl && d.moved) {
      await edit("เพิ่มเส้นแล้ว", () => call("addLine", st.page, nl[0][0], nl[0][1], nl[1][0], nl[1][1],
        +$("lw").value || 1, $("lcolor").value));
    }
    return;
  }
  if (st.lineDrag) {
    const dist = st.lineDrag.d;
    st.lineDrag = null;
    if (Math.abs(dist) > 0.3 && st.lineSel.length) await moveLine(dist);
    else drawOverlay();
  }
}

// ── โหมดข้อความ ─────────────────────────────────────────────────────
function clearTextSel() {
  st.spans = [];
  st.sel = null;
  st.cur = null;
  $("spanlist").innerHTML = '<li class="muted small">คลิกที่ข้อความบนหน้า หรือลากกรอบ</li>';
  $("oldtext").textContent = "";
  $("newtext").value = "";
  $("fontnote").textContent = "";
  $("fontnote").className = "note";
}

async function releaseText(isBox, x0, y0, x1, y1) {
  let rect;
  if (!isBox) {
    st.sel = null;
    rect = [x0 - 1, y0 - 1, x0 + 1, y0 + 1];
  } else {
    st.sel = rect = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
  }
  const spans = await task("", () => call("spansAt", st.page, ...rect, isBox));
  if (!spans) return;
  st.spans = spans;
  if (!spans.length && !st.sel) {
    clearTextSel();
    drawOverlay();
    status("ไม่พบข้อความตรงนั้น — ถ้าเป็นรูปภาพ/ไฟล์สแกนจะแก้ข้อความไม่ได้ (ลากกรอบเพื่อวางข้อความใหม่ได้)");
    return;
  }
  // ค่าเริ่มต้น: คลิก → ชั้นบนสุด, ลากกรอบ → ชิ้นนั้นถ้ามีชิ้นเดียว ไม่งั้นทั้งกรอบ
  const first = !st.sel ? 0 : spans.length === 1 ? 0 : -1;
  renderSpanList();
  await pickSpan(first);
  status(spans.length > 1 ? `พบข้อความ ${spans.length} ชิ้นในบริเวณนี้ — เลือกชิ้นที่จะแก้จากรายการด้านขวา` : "");
  $("newtext").focus();
}

function renderSpanList() {
  const ul = $("spanlist");
  ul.replaceChildren();
  const add = (idx, title, meta) => {
    const li = document.createElement("li");
    li.className = "pick";
    li.dataset.idx = idx;
    li.textContent = title;
    if (meta) {
      const m = document.createElement("span");
      m.className = "meta";
      m.textContent = meta;
      li.appendChild(m);
    }
    li.onclick = () => pickSpan(idx);
    ul.appendChild(li);
  };
  if (st.sel) add(-1, "▣ ทั้งกรอบ", "ทุกชั้นรวมกัน / วางข้อความใหม่ในกรอบ");
  const n = st.spans.length;
  st.spans.forEach((s, i) => {
    const tag = n > 1 ? `ชั้น ${n - i}` : "ข้อความ";
    const extra = (s.hidden ? " [มองไม่เห็น]" : "") + (s.layer ? ` [เลเยอร์ ${s.layer}]` : "");
    const txt = s.text.trim();
    add(i, `${tag}${extra}: ${txt.length <= 40 ? txt : txt.slice(0, 39) + "…"}`, `${s.font} · ${s.size} pt`);
  });
}

async function pickSpan(idx) {
  st.cur = idx;
  for (const li of $("spanlist").children) li.classList.toggle("sel", +li.dataset.idx === idx);
  let text, size, color;
  if (idx < 0) {
    ({ text, size, color } = await task("", () => call("inspectBox")) || {});
  } else {
    ({ text, size, color } = st.spans[idx]);
  }
  $("oldtext").textContent = text || "";
  $("newtext").value = text || ""; // เติมข้อความเดิมให้แก้ต่อได้เลย
  $("size").value = Math.round((size || 11) * 10) / 10;
  $("color").value = color || "#000000";
  drawOverlay();
  updateButtons();
  showFont();
}

let fontTimer;
function showFont() {
  clearTimeout(fontTimer);
  fontTimer = setTimeout(async () => {
    if (!st.doc || st.cur === null) return;
    const r = await call("pickFont", st.cur, $("newtext").value, $("autofont").checked, manualFont())
      .catch((e) => ({ ok: false, note: e.message }));
    renderFontNote(r);
  }, 150);
}
function renderFontNote(r) {
  const el = $("fontnote");
  el.className = "note " + (r.ok ? "ok" : "warn");
  el.textContent = r.note;
  if (!r.ok && r.missing) {
    const b = document.createElement("button");
    b.className = "btn";
    b.type = "button";
    b.textContent = "เพิ่มฟอนต์…";
    b.onclick = () => $("fontfile").click();
    el.appendChild(document.createElement("br"));
    el.appendChild(b);
  }
}
function manualFont() {
  const v = $("manualfont").value;
  return v === "auto" ? "" : v;
}

async function applyText() {
  if (st.cur === null) return status("คลิกที่ข้อความ หรือลากกรอบบริเวณที่ต้องการแก้ก่อน", true);
  const text = $("newtext").value;
  await edit(null, async () => {
    const f = await call("pickFont", st.cur, text, $("autofont").checked, manualFont());
    await call("applyText", st.cur, text, +$("size").value || 11, $("color").value, $("erase").checked, f.path);
    clearTextSel();
    return "แก้แล้ว — " + f.note;
  });
}

// ทำคำสั่งแก้ไฟล์ แล้ววาดหน้าใหม่
async function edit(done, fn) {
  return task("กำลังแก้ไข", async () => {
    let msg = done;
    try {
      const r = await fn();
      if (typeof r === "string") msg = r;
    } catch (e) {
      await refresh();
      throw new Error("แก้ไขไม่สำเร็จ: " + e.message);
    }
    st.dirty = true;
    st.undo = (await call("info")).undo;
    await refresh();
    status(msg || "");
    return true;
  });
}

// ── โหมดเส้น ───────────────────────────────────────────────────────
function hitSeg(vx, vy) {
  const [px, py] = toPage(vx, vy);
  let best = -1, bestD = Infinity;
  st.segs.forEach((s, i) => {
    const [along, across] = s.o === "h" ? [px, py] : [py, px];
    if (along < s.a - 2 || along > s.b + 2) return;
    const d = Math.abs(across - s.pos);
    if (d <= 5 / st.zoom + s.width / 2 && d < bestD) { best = i; bestD = d; }
  });
  return [best, [px, py]];
}

async function pressLine(pt) {
  const [i, ppt] = hitSeg(...pt);
  if (i < 0) {
    st.lineDrag = null;
    st.lineSel = [];
    updateLineInfo();
    drawOverlay();
    return;
  }
  st.lineDrag = { start: ppt, d: 0 };
  if (!st.lineSel.includes(i)) {
    st.lineSel = $("group").checked ? await call("lineGroup", i) : [i];
    const s = st.segs[i];
    $("lw").value = Math.round(s.width * 100) / 100;
    $("lcolor").value = s.color;
  }
  updateLineInfo();
  drawOverlay();
}

function updateLineInfo() {
  const sel = st.lineSel.map((i) => st.segs[i]);
  if (!sel.length) {
    $("lineinfo").textContent = "ยังไม่ได้เลือกเส้น";
  } else {
    const lo = Math.min(...sel.map((s) => s.a)), hi = Math.max(...sel.map((s) => s.b));
    $("lineinfo").textContent = `เส้น${sel[0].o === "h" ? "แนวนอน" : "แนวตั้ง"} ยาว ${(hi - lo).toFixed(0)} pt หนา ${sel[0].width.toFixed(2)} pt (${sel.length} ท่อน)`;
  }
  updateButtons();
}

async function lineEdit(done, opts) {
  if (!st.lineSel.length) return status("คลิกเลือกเส้นก่อน");
  const sel = st.lineSel.map((i) => st.segs[i]);
  const o = sel[0].o, pos = sel[0].pos + (opts.delta || 0);
  const a = Math.min(...sel.map((s) => s.a)), b = Math.max(...sel.map((s) => s.b));
  const idxs = st.lineSel;
  st.lineSel = [];
  const ok = await edit(done, () => call("editLines", st.page, idxs, opts.delta || 0, opts.width || 0,
    opts.color || "", !!opts.delete, $("stretch").checked));
  // หลังแก้ รหัสเส้นเปลี่ยน → หาเส้นเดิมจากตำแหน่งใหม่เพื่อคงการเลือกไว้
  if (ok && !opts.delete) {
    const i = st.segs.findIndex((s) => s.o === o && Math.abs(s.pos - pos) <= 0.6 && s.a <= b && s.b >= a);
    if (i >= 0) st.lineSel = $("group").checked ? await call("lineGroup", i) : [i];
  }
  updateLineInfo();
  drawOverlay();
}
const moveLine = (d) => lineEdit(`ย้ายเส้น ${d.toFixed(1)} pt`, { delta: d });

function nudgeLine(dx, dy) {
  if (st.mode !== "line" || !st.lineSel.length || st.busy) return;
  // ลูกศรอ้างทิศบนจอ → แปลงเป็นทิศในพิกัดจริงของหน้า (เผื่อหน้าถูกหมุน)
  const [ax, ay] = toPage(0, 0), [bx, by] = toPage(dx, dy);
  const d = st.segs[st.lineSel[0]].o === "h" ? by - ay : bx - ax;
  if (Math.abs(d) > 0.5) moveLine(d > 0 ? 0.5 : -0.5);
}

// ── โหมด ───────────────────────────────────────────────────────────
async function setMode(m) {
  st.mode = m;
  $("mode-text").classList.toggle("on", m === "text");
  $("mode-line").classList.toggle("on", m === "line");
  $("panel-text").hidden = m !== "text";
  $("panel-line").hidden = m !== "line";
  clearTextSel();
  st.lineSel = [];
  updateLineInfo();
  if (st.doc) await task("", refresh);
}

function updateButtons() {
  const has = !!st.doc && st.ready;
  const idle = !st.busy;
  $("save").disabled = !has || !idle;
  $("undo").disabled = !has || !st.undo || !idle;
  $("prev").disabled = !has || st.page <= 0;
  $("next").disabled = !has || st.page >= st.doc.pages.length - 1;
  $("apply").disabled = !has || st.cur === null || !idle;
  $("linestyle").disabled = $("linedel").disabled = !has || !st.lineSel.length || !idle;
}

// ── ฟอนต์ที่ผู้ใช้เพิ่ม (เก็บใน IndexedDB ของเบราว์เซอร์นี้) ─────────
function db() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("pdf-editor", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("fonts", { keyPath: "name" });
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
async function dbDo(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const tx = d.transaction("fonts", mode);
    const req = fn(tx.objectStore("fonts"));
    tx.oncomplete = () => resolve(req && req.result);
    tx.onerror = () => reject(tx.error);
  });
}
async function restoreFonts() {
  try {
    const all = (await dbDo("readonly", (s) => s.getAll())) || [];
    if (all.length) st.userFonts = await call("addFonts", all);
  } catch (e) {
    console.warn("restore fonts", e);
  }
  renderFonts();
}
async function addFontFiles(files) {
  const list = [];
  for (const f of files) {
    if (!/\.(ttf|otf|ttc)$/i.test(f.name)) continue;
    const data = await f.arrayBuffer();
    list.push({ name: f.name.replace(/[\\/]/g, "_"), data });
  }
  if (!list.length) return;
  try {
    for (const f of list) await dbDo("readwrite", (s) => s.put({ name: f.name, data: f.data.slice(0) }));
  } catch (e) {
    console.warn("save font", e); // เก็บถาวรไม่ได้ (เช่นโหมดส่วนตัว) ก็ยังใช้ได้ในรอบนี้
  }
  st.userFonts = await task("กำลังเพิ่มฟอนต์", () => call("addFonts", list)) || st.userFonts;
  renderFonts();
  status(`เพิ่มฟอนต์ ${list.map((f) => f.name).join(", ")} แล้ว`);
  showFont();
}
async function removeFont(name) {
  try { await dbDo("readwrite", (s) => s.delete(name)); } catch (_) {}
  st.userFonts = await call("removeFont", name);
  renderFonts();
  showFont();
}
function renderFonts() {
  const ul = $("fontlist");
  ul.replaceChildren();
  const files = [...new Set(st.userFonts.map((f) => f.file))];
  for (const f of BUNDLED_FONTS) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${f} <span class="meta">มากับโปรแกรม (ฟอนต์สำรอง)</span></span>`;
    ul.appendChild(li);
  }
  for (const f of files) {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = f;
    const btn = document.createElement("button");
    btn.className = "btn danger";
    btn.type = "button";
    btn.textContent = "ลบ";
    btn.onclick = () => removeFont(f);
    li.append(span, btn);
    ul.appendChild(li);
  }
  const sel = $("manualfont");
  const cur = sel.value;
  sel.replaceChildren();
  const opt = (v, t) => { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o); };
  opt("auto", "อัตโนมัติ (Sarabun ตามน้ำหนักตัวอักษร)");
  for (const f of BUNDLED_FONTS) opt("/fonts/bundled/" + f, f);
  for (const f of files) opt("/fonts/user/" + f, f);
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "auto";
}

// ── ผูกเหตุการณ์ ────────────────────────────────────────────────────
$("file").onchange = (e) => { openFile(e.target.files[0]); e.target.value = ""; };
$("save").onclick = save;
$("undo").onclick = undo;
$("prev").onclick = () => goto(st.page - 1);
$("next").onclick = () => goto(st.page + 1);
$("zoomin").onclick = () => setZoom(st.zoom + 0.25);
$("zoomout").onclick = () => setZoom(st.zoom - 0.25);
$("mode-text").onclick = () => setMode("text");
$("mode-line").onclick = () => setMode("line");
$("apply").onclick = applyText;
$("newtext").oninput = showFont;
$("autofont").onchange = showFont;
$("manualfont").onchange = showFont;
$("linestyle").onclick = () => lineEdit("เปลี่ยนความหนา/สีแล้ว", { width: +$("lw").value || 1, color: $("lcolor").value });
$("linedel").onclick = () => lineEdit("ลบเส้นแล้ว", { delete: true });
$("addline").onchange = () => { st.lineSel = []; updateLineInfo(); drawOverlay(); };
$("fontsbtn").onclick = () => $("fontsdlg").showModal();
$("fontfile").onchange = (e) => { addFontFiles([...e.target.files]); e.target.value = ""; };

const ov = $("overlay");
ov.addEventListener("pointerdown", onDown);
ov.addEventListener("pointermove", onMove);
ov.addEventListener("pointerup", onUp);
ov.addEventListener("pointercancel", () => { st.drag = null; st.lineDrag = null; st.newLine = null; drawOverlay(); });

document.addEventListener("keydown", (e) => {
  const typing = /^(TEXTAREA|INPUT|SELECT)$/.test(document.activeElement.tagName);
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); undo(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (st.doc) save(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && st.mode === "text") { e.preventDefault(); applyText(); return; }
  if (typing || st.mode !== "line") return;
  const dirs = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
  if (dirs[e.key] && st.lineSel.length) { e.preventDefault(); nudgeLine(...dirs[e.key]); }
});

// ลากไฟล์มาวาง: PDF → เปิด, ฟอนต์ → เพิ่มฟอนต์
const viewer = $("viewer");
viewer.addEventListener("dragover", (e) => { e.preventDefault(); viewer.classList.add("dragover"); });
viewer.addEventListener("dragleave", () => viewer.classList.remove("dragover"));
viewer.addEventListener("drop", (e) => {
  e.preventDefault();
  viewer.classList.remove("dragover");
  const files = [...e.dataTransfer.files];
  const pdf = files.find((f) => /\.pdf$/i.test(f.name) || f.type === "application/pdf");
  const fonts = files.filter((f) => /\.(ttf|otf|ttc)$/i.test(f.name));
  if (fonts.length) addFontFiles(fonts);
  if (pdf) openFile(pdf);
});

window.addEventListener("beforeunload", (e) => { if (st.dirty) { e.preventDefault(); e.returnValue = ""; } });

renderFonts();
updateButtons();
boot();
