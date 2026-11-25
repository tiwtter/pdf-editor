// Service Worker: เก็บไฟล์โปรแกรมไว้ในเบราว์เซอร์ → เปิดใช้ได้แม้ไม่มีอินเทอร์เน็ต
// - ไฟล์ของ Pyodide/PyMuPDF (ไม่เปลี่ยนตามเวอร์ชัน) → ใช้จากที่เก็บไว้ก่อน (cache-first)
// - ไฟล์ของเว็บนี้ → ลองเน็ตก่อน 4 วินาที ไม่ได้ก็ใช้ที่เก็บไว้ (ได้อัปเดตเมื่อออนไลน์)
// ไฟล์ PDF ของผู้ใช้ไม่ผ่านที่นี่ (เปิดจากเครื่องโดยตรง)
const APP_CACHE = "pdf-editor-app-v4";
const LIB_CACHE = "pdf-editor-lib-pyodide-0.29.3";
const SHELL = [
  "./", "index.html", "style.css", "app.js", "worker.js", "bridge.py", "icon.svg",
  "../pdf_core.py", "fonts/Sarabun-Regular.ttf", "fonts/Sarabun-Bold.ttf",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(APP_CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" })))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) {
      if (k !== APP_CACHE && k !== LIB_CACHE) await caches.delete(k);
    }
    await self.clients.claim();
  })());
});

async function cacheFirst(req) {
  const cache = await caches.open(LIB_CACHE);
  const hit = await cache.match(req.url, { ignoreVary: true });
  if (hit) return hit;
  // importScripts ขอแบบ no-cors (ได้คำตอบที่อ่านไม่ได้และเก็บไม่ได้) → ขอใหม่แบบ cors เสมอ
  const res = await fetch(req.url, { mode: "cors", credentials: "omit" });
  if (res.ok) await cache.put(req.url, res.clone());
  return res;
}

async function networkFirst(req) {
  const cache = await caches.open(APP_CACHE);
  const key = new URL(req.url);
  key.search = "";
  try {
    const res = await Promise.race([
      fetch(req, { cache: "no-cache" }), // ตรวจกับเซิร์ฟเวอร์ทุกครั้ง ไม่ใช้ของเก่าใน HTTP cache
      new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 4000)),
    ]);
    if (res.ok) await cache.put(key.href, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(key.href, { ignoreVary: true });
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname === "cdn.jsdelivr.net" && url.pathname.startsWith("/pyodide/")) {
    e.respondWith(cacheFirst(req));
  } else if (url.origin === self.location.origin && !url.pathname.endsWith(".pdf")) {
    e.respondWith(networkFirst(req));
  }
});
