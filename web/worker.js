// Web Worker: รัน Python (Pyodide) + PyMuPDF แยกจากหน้าเว็บ หน้าจอจะได้ไม่ค้างตอนประมวลผล
// ไฟล์ PDF และฟอนต์อยู่ในหน่วยความจำของเบราว์เซอร์เท่านั้น ไม่ถูกส่งไปที่ใด
const PYODIDE_VERSION = "0.29.3"; // มี PyMuPDF 1.26.3 มาให้ในชุด
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

// ไฟล์ใหญ่ที่ต้องดาวน์โหลดครั้งแรก (ดึงเองก่อนเพื่อแสดงความคืบหน้า แล้ว Service Worker เก็บไว้ใช้ออฟไลน์)
const BIG_FILES = [
  ["pyodide.asm.wasm", 8.6e6],
  ["python_stdlib.zip", 2.4e6],
  ["pymupdf-1.26.3-cp313-none-pyodide_2025_0_wasm32.whl", 17.3e6],
  ["pyodide.asm.js", 1.1e6],
  ["pyodide.js", 0.02e6],
];

const APP_FILES = [
  ["../pdf_core.py", "/app/pdf_core.py"],
  ["bridge.py", "/app/bridge.py"],
  ["fonts/Sarabun-Regular.ttf", "/fonts/bundled/Sarabun-Regular.ttf"],
  ["fonts/Sarabun-Bold.ttf", "/fonts/bundled/Sarabun-Bold.ttf"],
];

let py, bridge;

const progress = (text, frac) => postMessage({ type: "progress", text, frac });

async function prefetch() {
  const total = BIG_FILES.reduce((s, f) => s + f[1], 0);
  let done = 0;
  for (const [name, size] of BIG_FILES) {
    let got = 0;
    for (let attempt = 1; ; attempt++) {
      try {
        const res = await fetch(PYODIDE_URL + name);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const reader = res.body.getReader();
        got = 0;
        for (;;) {
          const { done: end, value } = await reader.read();
          if (end) break;
          got += value.length;
          progress(`กำลังดาวน์โหลดตัวแก้ PDF (ครั้งแรกเท่านั้น) ${((done + got) / 1e6).toFixed(1)} / ${(Math.max(total, done + got) / 1e6).toFixed(0)} MB`,
            Math.min(0.95, (done + got) / total));
        }
        break;
      } catch (e) {
        if (attempt >= 4) throw new Error(`ดาวน์โหลด ${name} ไม่สำเร็จ (${e.message}) — ตรวจอินเทอร์เน็ตแล้วโหลดหน้าใหม่`);
        progress(`เน็ตหลุด กำลังลองใหม่ (${attempt}/3)…`, done / total);
        await new Promise((r) => setTimeout(r, 1500 * attempt));
      }
    }
    done += Math.max(size, got);
  }
}

async function init() {
  progress("กำลังเตรียมโปรแกรม…", 0);
  await prefetch();
  progress("กำลังเปิด Python…", 0.96);
  importScripts(PYODIDE_URL + "pyodide.js");
  py = await loadPyodide({ indexURL: PYODIDE_URL });
  progress("กำลังโหลด PyMuPDF…", 0.98);
  await py.loadPackage("pymupdf");
  for (const d of ["/app", "/fonts", "/fonts/bundled", "/fonts/user"]) {
    try { py.FS.mkdir(d); } catch (_) {}
  }
  for (const [url, dest] of APP_FILES) {
    const res = await fetch(url, { cache: "no-cache" }).catch(() => fetch(url));
    if (!res.ok) throw new Error(`โหลด ${url} ไม่สำเร็จ`);
    py.FS.writeFile(dest, new Uint8Array(await res.arrayBuffer()));
  }
  py.runPython("import sys; sys.path.insert(0, '/app')");
  bridge = py.pyimport("bridge");
}

// ข้อความผิดพลาดจาก Python: เอาเฉพาะบรรทัดสุดท้าย (ชนิด: ข้อความ)
function pyError(e) {
  const msg = String(e && e.message ? e.message : e);
  const lines = msg.trim().split("\n");
  const last = lines[lines.length - 1] || msg;
  return last.replace(/^\w+(Error|Exception): /, "");
}

const J = (s) => JSON.parse(s);

const handlers = {
  init: async () => { await init(); return { ok: true }; },

  open: (bytes, name) => {
    py.FS.writeFile("/tmp/in.pdf", new Uint8Array(bytes));
    return J(bridge.open_pdf("/tmp/in.pdf"));
  },
  info: () => J(bridge.info()),
  render: (page, scale) => {
    const meta = J(bridge.render(page, scale));
    const rgb = py.FS.readFile("/tmp/page.raw");
    const rgba = new Uint8ClampedArray(meta.w * meta.h * 4);
    for (let y = 0; y < meta.h; y++) {
      let s = y * meta.stride, d = y * meta.w * 4;
      for (let x = 0; x < meta.w; x++, s += 3, d += 4) {
        rgba[d] = rgb[s]; rgba[d + 1] = rgb[s + 1]; rgba[d + 2] = rgb[s + 2]; rgba[d + 3] = 255;
      }
    }
    return { transfer: [rgba.buffer], value: { w: meta.w, h: meta.h, data: rgba } };
  },
  save: () => {
    bridge.save("/tmp/out.pdf");
    const data = py.FS.readFile("/tmp/out.pdf");
    const copy = data.slice();
    return { transfer: [copy.buffer], value: copy };
  },
  undo: () => J(bridge.undo()),

  addFonts: (files) => {
    for (const f of files) py.FS.writeFile("/fonts/user/" + f.name, new Uint8Array(f.data));
    return J(bridge.fonts_changed());
  },
  removeFont: (name) => {
    try { py.FS.unlink("/fonts/user/" + name); } catch (_) {}
    return J(bridge.fonts_changed());
  },

  spansAt: (page, x0, y0, x1, y1, isBox) => J(bridge.spans_at(page, x0, y0, x1, y1, isBox)),
  inspectBox: () => J(bridge.inspect_box()),
  pickFont: (idx, text, auto, manual) => J(bridge.pick_font(idx, text, auto, manual || "")),
  applyText: (idx, text, size, color, erase, fontpath) =>
    J(bridge.apply_text(idx, text, size, color, erase, fontpath || "")),

  segments: (page) => J(bridge.segments(page)),
  lineGroup: (i) => J(bridge.line_group(i)),
  fromView: (page, x, y) => J(bridge.from_view(page, x, y)),
  editLines: (page, idxs, delta, width, color, del, stretch) =>
    J(bridge.edit_lines(page, py.toPy(idxs), delta, width || 0, color || "", del, stretch)),
  addLine: (page, x0, y0, x1, y1, width, color) => J(bridge.add_line(page, x0, y0, x1, y1, width, color)),
};

onmessage = async (ev) => {
  const { id, cmd, args } = ev.data;
  try {
    let out = await handlers[cmd](...(args || []));
    let transfer = [];
    if (out && out.transfer) { transfer = out.transfer; out = out.value; }
    postMessage({ id, ok: true, result: out }, transfer);
  } catch (e) {
    console.error(e);
    postMessage({ id, ok: false, error: pyError(e) });
  }
};
