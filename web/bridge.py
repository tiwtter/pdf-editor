# -*- coding: utf-8 -*-
"""ตัวเชื่อมระหว่างหน้าเว็บ (JavaScript) กับ PdfCore — รันใน Pyodide (Python บนเบราว์เซอร์)

ค่าที่ส่งกลับเป็น JSON string ส่วนข้อมูลใหญ่ (ภาพหน้า / ไฟล์ PDF) เขียนลงไฟล์ในหน่วยความจำ
แล้วให้ JavaScript อ่านด้วย pyodide.FS.readFile

พิกัด: "view" = พิกัดตามที่เห็นบนจอ (หลังหมุนหน้า) หน่วย pt — หน้าเว็บใช้พิกัดนี้ทั้งหมด
"""
import json
import os
import sys

sys.path.insert(0, "/app")
import pdf_core  # noqa: E402
from pdf_core import PdfCore, fitz, norm_font  # noqa: E402

USER_DIR = "/fonts/user"           # ฟอนต์ที่ผู้ใช้เพิ่มเอง (แทนโฟลเดอร์ฟอนต์ของระบบ)
BUNDLED_DIR = "/fonts/bundled"     # ฟอนต์ไทยที่มากับเว็บ
FALLBACK = BUNDLED_DIR + "/Sarabun-Regular.ttf"
FALLBACK_BOLD = BUNDLED_DIR + "/Sarabun-Bold.ttf"
os.makedirs(USER_DIR, exist_ok=True)

# เบราว์เซอร์อ่านฟอนต์ในเครื่องไม่ได้ → ให้ FontIndex ค้นเฉพาะฟอนต์ที่ผู้ใช้เพิ่ม
pdf_core.FONT_DIRS[:] = [USER_DIR]
pdf_core.FONT_CANDIDATES[:] = [FALLBACK]

core = PdfCore()
state = dict(page=0, spans=[], sel=None, segs=[])


def _j(x):
    return json.dumps(x, ensure_ascii=False)


def _page():
    return core.doc[state["page"]]


def _view_rect(page, r):
    r = fitz.Rect(r)
    if page.rotation:
        r = r * page.rotation_matrix
        r.normalize()
    return [r.x0, r.y0, r.x1, r.y1]


def _view_pt(page, p):
    p = fitz.Point(p) * page.rotation_matrix
    return [p.x, p.y]


def _hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in c)


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ── เอกสาร ──────────────────────────────────────────────────────────
def open_pdf(path):
    core.open(path)
    state.update(page=0, spans=[], sel=None, segs=[])
    return info()


def info():
    pages = []
    for p in core.doc:
        r = p.rect                                  # กรอบตามที่เห็น (หมุนแล้ว)
        pages.append(dict(w=r.width, h=r.height, rot=p.rotation,
                          m=list(p.rotation_matrix), dm=list(p.derotation_matrix)))
    return _j(dict(pages=pages, undo=len(core.undo_stack), version=fitz.VersionBind))


def render(page_no, scale, out="/tmp/page.raw"):
    """วาดหน้าเป็นพิกเซล RGB ลงไฟล์ out คืนขนาดภาพ"""
    state["page"] = page_no
    pix = core.doc[page_no].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    with open(out, "wb") as f:
        f.write(pix.samples)
    return _j(dict(w=pix.width, h=pix.height, n=pix.n, stride=pix.stride))


def save(out="/tmp/out.pdf"):
    core.save(out)
    return os.path.getsize(out)


def undo():
    ok = core.undo()
    state.update(spans=[], sel=None)
    return _j(dict(ok=ok, undo=len(core.undo_stack)))


# ── ฟอนต์ ───────────────────────────────────────────────────────────
def fonts_changed():
    """เรียกหลังเพิ่ม/ลบไฟล์ใน USER_DIR ให้ดัชนีฟอนต์สร้างใหม่"""
    core.fonts._map = None
    core._font_objs.clear()
    names = []
    for fn in sorted(os.listdir(USER_DIR)):
        try:
            for _, keys in core.fonts._faces(os.path.join(USER_DIR, fn)):
                names.append(dict(file=fn, keys=sorted(keys)))
        except Exception as e:
            names.append(dict(file=fn, error=str(e)))
    return _j(names)


def _fallback_for(fontname):
    return FALLBACK_BOLD if "bold" in norm_font(fontname or "") else FALLBACK


def pick_font(idx, text, auto, manual):
    """เลือกไฟล์ฟอนต์สำหรับแก้ข้อความ — ตรรกะเดียวกับ _pick_fontfile ของโปรแกรมบนเครื่อง

    idx = ชิ้นที่เลือก (-1 = ทั้งกรอบ), manual = พาธฟอนต์ที่ผู้ใช้เลือกเอง หรือ ""
    """
    spans = state["spans"]
    name = spans[idx]["font"] if 0 <= idx < len(spans) else (spans[0]["font"] if spans else None)
    fallback = manual or _fallback_for(name)
    fb_name = os.path.basename(fallback)
    if not auto or not name or not core.doc:
        return _j(dict(path=fallback, ok=True, note="ฟอนต์: " + fb_name))
    path, src = core.resolve_font(state["page"], name, text)
    if src == "system":
        return _j(dict(path=path, ok=True, note="ฟอนต์เดิม: %s ✓ ใช้ฟอนต์ที่คุณเพิ่มไว้" % name))
    if src == "embedded":
        return _j(dict(path=path, ok=True, note="ฟอนต์เดิม: %s ✓ ใช้ฟอนต์ที่ฝังในไฟล์" % name))
    return _j(dict(path=fallback, ok=False, missing=name,
                   note="ฟอนต์เดิม: %s ✗ ไม่มีในรายการฟอนต์ และฟอนต์ที่ฝังในไฟล์มีตัวอักษรไม่ครบ "
                        "→ ใช้ %s แทน (กด “เพิ่มฟอนต์” แล้วเลือกไฟล์ฟอนต์ %s เพื่อให้เหมือนเดิม)"
                        % (name, fb_name, name)))


# ── ข้อความ ─────────────────────────────────────────────────────────
def spans_at(page_no, x0, y0, x1, y1, is_box):
    """ข้อความทุกชั้นในกรอบ (พิกัด view) เรียงชั้นบนสุดก่อน"""
    state["page"] = page_no
    rect = (x0, y0, x1, y1)
    state["sel"] = rect if is_box else None
    spans = core.spans_at(page_no, rect)
    state["spans"] = spans
    page = core.doc[page_no]
    return _j([dict(text=s["text"], font=s["font"], size=s["size"], color=_hex(s["color"]),
                    bbox=_view_rect(page, s["bbox"]), hidden=s["hidden"], layer=s["layer"])
               for s in spans])


def inspect_box():
    text, size, color = core.inspect(state["page"], state["sel"])
    return _j(dict(text=text, size=size, color=_hex(color)))


def apply_text(idx, text, size, color, erase, fontpath):
    kw = dict(fontfile=fontpath or None, size=float(size), color=_rgb(color), erase_old=bool(erase))
    if 0 <= idx < len(state["spans"]):
        core.edit_span(state["page"], state["spans"][idx], text, **kw)
    elif state["sel"]:
        core.edit(state["page"], state["sel"], text, **kw)
    else:
        raise RuntimeError("คลิกที่ข้อความ หรือลากกรอบบริเวณที่ต้องการแก้ก่อน")
    state.update(spans=[], sel=None)
    return _j(dict(undo=len(core.undo_stack)))


# ── เส้นตาราง ──────────────────────────────────────────────────────
def segments(page_no):
    state["page"] = page_no
    segs = core.segments(page_no)
    state["segs"] = segs
    page = core.doc[page_no]
    out = []
    for i, s in enumerate(segs):
        p1, p2 = PdfCore.seg_points(s)
        out.append(dict(i=i, o=s["o"], pos=s["pos"], a=s["a"], b=s["b"], width=s["width"],
                        color=_hex(s["color"]), p1=_view_pt(page, p1), p2=_view_pt(page, p2)))
    return _j(out)


def line_group(i):
    segs = state["segs"]
    ids = {id(s) for s in PdfCore.line_group(segs, segs[i])}
    return _j([k for k, s in enumerate(segs) if id(s) in ids])


def from_view(page_no, x, y):
    return _j(list(core.from_view(page_no, x, y)))


def edit_lines(page_no, idxs, delta, width, color, delete, stretch):
    segs = state["segs"]
    ids = [segs[i]["id"] for i in idxs]
    core.edit_lines(page_no, ids, delta=float(delta), width=float(width) if width else None,
                    color=_rgb(color) if color else None, delete=bool(delete), stretch=bool(stretch))
    return _j(dict(undo=len(core.undo_stack)))


def add_line(page_no, vx0, vy0, vx1, vy1, width, color):
    p1 = core.from_view(page_no, vx0, vy0)
    p2 = core.from_view(page_no, vx1, vy1)
    core.add_line(page_no, p1, p2, width=float(width), color=_rgb(color))
    return _j(dict(undo=len(core.undo_stack)))
