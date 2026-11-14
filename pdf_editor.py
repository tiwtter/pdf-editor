#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF Editor ต้นแบบ — แก้ข้อความทีละชั้น และปรับเส้นตารางในไฟล์ PDF

ติดตั้ง:   pip install pymupdf
รัน:       python pdf_editor.py [ไฟล์.pdf]

โครงสร้างไฟล์นี้แบ่งเป็น 3 ส่วน
  1) FontIndex — หาไฟล์ฟอนต์ในเครื่องจากชื่อฟอนต์ที่ PDF ใช้
  2) PdfCore   — ตรรกะแก้ PDF ล้วน ๆ (ไม่มี GUI) นำไปใช้ซ้ำในสคริปต์อื่นได้
  3) EditorApp — หน้าต่าง tkinter มี 2 โหมด: ข้อความ / เส้นตาราง
"""
import collections
import html
import os
import re
import shutil
import struct
import sys
import tempfile
try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk
except ImportError:
    sys.exit("Python นี้ไม่มี tkinter — Linux: sudo apt install python3-tk | "
             "macOS (Homebrew): brew install python-tk")

try:
    import pymupdf as fitz  # PyMuPDF รุ่นใหม่
except ImportError:
    try:
        import fitz  # PyMuPDF รุ่นเก่า
    except ImportError:
        sys.exit("ยังไม่ได้ติดตั้ง PyMuPDF — รัน: pip install pymupdf")


# ฟอนต์สำรอง ใช้เมื่อหาฟอนต์เดิมของข้อความไม่เจอ (ตัวแรกที่เจอจะถูกใช้)
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\THSarabunNew.ttf",
    r"C:\Windows\Fonts\LeelawUI.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    "/Library/Fonts/THSarabunNew.ttf",
    "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    "/usr/share/fonts/opentype/tlwg/Loma.otf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
]

# โฟลเดอร์ที่จะค้นหาฟอนต์ (รวมฟอนต์ที่มากับ Microsoft Office บน Mac)
FONT_DIRS = [
    "~/Library/Fonts", "/Library/Fonts", "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Applications/Microsoft Word.app/Contents/Resources/DFonts",
    "/Applications/Microsoft Excel.app/Contents/Resources/DFonts",
    "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts",
    r"C:\Windows\Fonts", "~/AppData/Local/Microsoft/Windows/Fonts",
    "/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts",
]


def find_default_font():
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


# ════════════════════════════════════════════════════════════════════
# ส่วนที่ 1: ดัชนีฟอนต์ในเครื่อง
# ════════════════════════════════════════════════════════════════════
def norm_font(name):
    """ชื่อฟอนต์ → คีย์เทียบ เช่น 'BCDEEE+CordiaNew-Bold' และ 'Cordia New Bold' → 'cordianewbold'"""
    name = re.sub(r"^[A-Z]{6}\+", "", name or "")
    name = re.sub(r"PS(?=-|MT|$)", "", name)
    name = re.sub(r"MT$", "", name)
    name = re.sub(r"[^0-9a-z\u0e00-\u0e7f]", "", name.lower())
    return re.sub(r"regular$", "", name)


class FontIndex:
    """อ่านชื่อฟอนต์จากตาราง 'name' ของไฟล์ .ttf/.otf/.ttc โดยตรง (เร็ว และรองรับ .ttc หลายหน้า)"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self._map = None                           # คีย์ → (ไฟล์, offset ของหน้าฟอนต์)
        self._extracted = {}

    @staticmethod
    def _faces(path):
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                return
            if head[:4] == b"ttcf":
                n = struct.unpack(">I", head[8:12])[0]
                offsets = struct.unpack(">%dI" % n, f.read(4 * n))
            else:
                offsets = (0,)
            for off in offsets:
                f.seek(off + 4)
                ntab = struct.unpack(">H", f.read(2))[0]
                f.seek(off + 12)
                recs = f.read(16 * ntab)
                names = {}
                for i in range(ntab):
                    tag, _, toff, tlen = struct.unpack(">4sIII", recs[16 * i:16 * i + 16])
                    if tag != b"name":
                        continue
                    f.seek(toff)
                    tb = f.read(tlen)
                    _, cnt, sto = struct.unpack(">HHH", tb[:6])
                    for j in range(cnt):
                        pid, _, _, nid, ln, so = struct.unpack(">6H", tb[6 + 12 * j:18 + 12 * j])
                        if nid not in (1, 2, 4, 6, 16, 17):
                            continue
                        raw = tb[sto + so:sto + so + ln]
                        try:
                            s = raw.decode("utf-16-be" if pid in (0, 3) else "latin-1")
                        except Exception:
                            continue
                        names.setdefault(nid, set()).add(s)
                keys = set()
                for nid in (4, 6):
                    keys |= {norm_font(s) for s in names.get(nid, ())}
                for fam in names.get(16, set()) | names.get(1, set()):
                    for sub in names.get(17, set()) | names.get(2, set()):
                        keys.add(norm_font(fam + sub))
                yield off, keys - {""}

    def _build(self):
        self._map = {}
        for d in FONT_DIRS:
            d = os.path.expanduser(d)
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                for fn in files:
                    if not fn.lower().endswith((".ttf", ".otf", ".ttc")):
                        continue
                    path = os.path.join(root, fn)
                    try:
                        for off, keys in self._faces(path):
                            for k in keys:
                                self._map.setdefault(k, (path, off))
                    except Exception:
                        continue

    def find(self, fontname):
        """คืนพาธไฟล์ฟอนต์ (เดี่ยว ๆ ใช้ได้ทันที) ที่ชื่อตรงกับ fontname หรือ None"""
        if self._map is None:
            self._build()
        hit = self._map.get(norm_font(fontname))
        if not hit:
            return None
        path, off = hit
        if not path.lower().endswith(".ttc"):
            return path
        if hit not in self._extracted:             # ดึงหน้าฟอนต์ออกจาก .ttc เป็น .ttf เดี่ยว
            with open(path, "rb") as f:
                data = f.read()
            ntab = struct.unpack(">H", data[off + 4:off + 6])[0]
            out = bytearray(data[off:off + 12])
            body = bytearray()
            base = 12 + 16 * ntab
            for i in range(ntab):
                tag, cs, toff, tlen = struct.unpack(">4sIII", data[off + 12 + 16 * i:off + 28 + 16 * i])
                out += struct.pack(">4sIII", tag, cs, base + len(body), tlen)
                body += data[toff:toff + tlen] + b"\0" * (-tlen % 4)
            dst = os.path.join(self.out_dir, "ttc%d.ttf" % len(self._extracted))
            with open(dst, "wb") as f:
                f.write(out + body)
            self._extracted[hit] = dst
        return self._extracted[hit]


# ════════════════════════════════════════════════════════════════════
# ส่วนที่ 2: แกนแก้ไข PDF
# ════════════════════════════════════════════════════════════════════
def _rgb(c):
    """สีจาก PDF (เทา / RGB / CMYK) → RGB 0-1"""
    if c is None:
        return (0, 0, 0)
    if isinstance(c, (int, float)):
        return (c, c, c)
    c = tuple(c)
    if len(c) == 1:
        return (c[0],) * 3
    if len(c) == 4:
        return tuple((1 - x) * (1 - c[3]) for x in c[:3])
    return c[:3]


def _ckey(c):
    """คีย์ระบุตัวอักษรหนึ่งตัวบนหน้า: (รหัสอักษร, ตำแหน่ง)"""
    return (c[0], round(c[2][0], 1), round(c[2][1], 1))


def _dkey(d):
    r = d["rect"]
    return (d["type"], round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1),
            len(d["items"]), str(d.get("fill")), str(d.get("color")), round(d.get("width") or 0, 2))


class PdfCore:
    """เก็บเอกสารไว้ในหน่วยความจำ แก้ข้อความ และแก้เส้น

    พิกัด: เมธอดที่รับ rect จากหน้าจอใช้พิกัด "ตามที่เห็น" (หลังหมุนหน้า)
           ที่เหลือใช้พิกัดจริงของหน้า (ก่อนหมุน) — แปลงด้วย to_view / from_view
    """

    MAX_UNDO = 20

    def __init__(self):
        self.doc = None
        self.undo_stack = []                      # สำเนาเอกสาร (bytes) ก่อนแก้แต่ละครั้ง
        self._font_dir = tempfile.mkdtemp(prefix="pdfedit_font_")
        self.fonts = FontIndex(self._font_dir)
        self._font_objs = {}
        self._font_names = {}

    # ── เปิด / บันทึก ────────────────────────────────────────────────
    def open(self, path):
        # อ่านเป็น bytes แล้วเปิดจากหน่วยความจำ → ไฟล์ต้นฉบับไม่ถูกล็อก
        with open(path, "rb") as f:
            data = f.read()
        self.doc = fitz.open(stream=data, filetype="pdf")
        self.undo_stack = []

    def save(self, path):
        try:
            self.doc.subset_fonts()               # ฝังเฉพาะตัวอักษรที่ใช้ → ไฟล์เล็กลง
        except Exception:
            pass
        self.doc.save(path, garbage=3, deflate=True)

    @property
    def page_count(self):
        return len(self.doc) if self.doc else 0

    # ── แสดงผล / พิกัด ───────────────────────────────────────────────
    def render(self, page_no, zoom):
        """คืนภาพของหน้า (PPM bytes) ที่ขยาย zoom เท่า"""
        page = self.doc[page_no]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("ppm"), pix.width, pix.height

    def to_view(self, page_no, x, y):
        p = fitz.Point(x, y) * self.doc[page_no].rotation_matrix
        return p.x, p.y

    def from_view(self, page_no, x, y):
        p = fitz.Point(x, y) * self.doc[page_no].derotation_matrix
        return p.x, p.y

    def _to_page_rect(self, page, rect):
        """กรอบที่เห็นบนจอ → กรอบจริงของหน้า (ต่างกันเมื่อหน้าถูกหมุน)"""
        r = fitz.Rect(rect)
        if page.rotation:
            r = r * page.derotation_matrix
        r.normalize()
        return r

    def _snapshot(self):
        self.undo_stack.append(self.doc.tobytes())
        del self.undo_stack[:-self.MAX_UNDO]

    def undo(self):
        if not self.undo_stack:
            return False
        self.doc = fitz.open(stream=self.undo_stack.pop(), filetype="pdf")
        return True

    # ── ฟอนต์ ────────────────────────────────────────────────────────
    def _font(self, path):
        if path not in self._font_objs:
            self._font_objs[path] = fitz.Font(fontfile=path)
        return self._font_objs[path]

    def _covers(self, font, text):
        return all(font.has_glyph(ord(ch)) for ch in set(text) if not ch.isspace())

    def resolve_font(self, page_no, fontname, text=""):
        """หาไฟล์ฟอนต์ที่ตรงกับฟอนต์เดิมของข้อความ

        คืน (พาธ, ที่มา)  ที่มา = 'system' ฟอนต์ในเครื่อง | 'embedded' ฟอนต์ที่ฝังใน PDF | None ไม่พบ
        """
        path = self.fonts.find(fontname)
        if path:
            return path, "system"
        # ฟอนต์ที่ฝังใน PDF มักมีเฉพาะตัวอักษรที่เอกสารใช้ → ใช้ได้เมื่อมีครบทุกตัวของข้อความใหม่
        key = norm_font(fontname)
        for f in self.doc[page_no].get_fonts(full=True):
            if norm_font(f[3]) != key:
                continue
            try:
                _, ext, _, buf = self.doc.extract_font(f[0])
            except Exception:
                continue
            if not buf or ext not in ("ttf", "otf"):
                continue
            path = os.path.join(self._font_dir, "emb%d.%s" % (f[0], ext))
            if not os.path.isfile(path):
                with open(path, "wb") as fh:
                    fh.write(buf)
            try:
                if self._covers(self._font(path), text):
                    return path, "embedded"
            except Exception:
                continue
        return None, None

    # ── อ่านข้อความ ──────────────────────────────────────────────────
    def inspect(self, page_no, rect):
        """คืน (ข้อความ, ขนาดฟอนต์, สี RGB 0-1) ของข้อความทั้งหมดในกรอบ (พิกัดตามที่เห็น)"""
        page = self.doc[page_no]
        r = self._to_page_rect(page, rect)
        text = page.get_text("text", clip=r).strip()
        size, color = 11.0, (0, 0, 0)
        for block in page.get_text("dict", clip=r).get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        size = round(span["size"], 1)
                        c = span.get("color", 0)   # จำนวนเต็ม 0xRRGGBB
                        color = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
                        return text, size, color
        return text, size, color

    def spans_at(self, page_no, rect):
        """ข้อความแต่ละชิ้น (span) ที่แตะกรอบ (พิกัดตามที่เห็น) เรียงจากชั้นบนสุดลงล่าง

        PDF วาดตามลำดับ ชิ้นที่วาดทีหลัง (seqno มาก) อยู่ชั้นบน
        """
        page = self.doc[page_no]
        r = self._to_page_rect(page, rect)
        out = []
        for s in page.get_texttrace():
            if not s["chars"] or not fitz.Rect(s["bbox"]).intersects(r):
                continue
            text = "".join(chr(c[0]) for c in s["chars"])
            if not text.strip():
                continue
            out.append(dict(seqno=s["seqno"], text=text, font=s["font"], size=round(s["size"], 2),
                            color=_rgb(s["color"]), bbox=tuple(s["bbox"]), dir=tuple(s["dir"]),
                            origin=tuple(s["chars"][0][2]), chars=s["chars"], layer=s.get("layer") or "",
                            opacity=s.get("opacity", 1.0), rmode=s.get("type", 0),
                            hidden=s.get("type", 0) == 3 or s.get("opacity", 1.0) == 0))
        out.sort(key=lambda s: -s["seqno"])
        return out

    # ── แก้ข้อความ: ทั้งกรอบ ─────────────────────────────────────────
    def _redact(self, page, rects, **kw):
        for r in rects:                            # ทำทีละกรอบ — ใส่หลายกรอบพร้อมกันแล้วลายเส้นถูกลบไม่ครบ
            page.add_redact_annot(r, fill=False)
            try:
                page.apply_redactions(**kw)
            except TypeError:                      # PyMuPDF รุ่นเก่า พารามิเตอร์ไม่ครบ
                page.apply_redactions(images=0)

    def erase(self, page, r):
        """ลบข้อความทุกชั้นในกรอบออกจากไฟล์จริง (ไม่แตะรูปและเส้น)"""
        self._redact(page, [r], images=0, graphics=0)

    def _html(self, text, fontfile, size, color, pre=False):
        rgb = "rgb(%d,%d,%d)" % tuple(int(c * 255) for c in color)
        css = "* {font-size:%gpx; color:%s; line-height:1.2; margin:0; padding:0;%s}" % (
            size, rgb, " white-space:pre;" if pre else "")
        archive = None
        if fontfile:
            # คัดลอกฟอนต์มาไว้ชื่อสั้น ๆ เพื่อเลี่ยงปัญหาช่องว่างในพาธ
            if fontfile not in self._font_names:
                name = "u%d%s" % (len(self._font_names), os.path.splitext(fontfile)[1].lower())
                shutil.copyfile(fontfile, os.path.join(self._font_dir, name))
                self._font_names[fontfile] = name
            name = self._font_names[fontfile]
            fam = name.split(".")[0]
            css = ("@font-face {font-family: %s; src: url(%s);} * {font-family: %s;} " % (fam, name, fam)) + css
            archive = fitz.Archive(self._font_dir)
        body = "<br>".join(html.escape(line) for line in text.split("\n"))
        return "<div>%s</div>" % body, css, archive

    def write(self, page, r, text, fontfile, size, color):
        """วางข้อความใหม่ในกรอบ (ย่ออัตโนมัติถ้าล้น)"""
        if hasattr(page, "insert_htmlbox"):
            # insert_htmlbox จัดเรียงสระ/วรรณยุกต์ไทยให้ถูกต้อง (ใช้ HarfBuzz)
            body, css, archive = self._html(text, fontfile, size, color)
            page.insert_htmlbox(r, body, css=css, archive=archive, rotate=page.rotation)
        else:
            # รุ่นเก่า: ไม่มีการจัดรูปอักษรไทย สระซ้อนอาจเพี้ยน
            kw = dict(fontsize=size, color=color)
            if fontfile:
                kw.update(fontname="userfont", fontfile=fontfile)
            if page.insert_textbox(r, text, **kw) < 0:      # ล้นกรอบ → วางบรรทัดเดียว
                page.insert_text((r.x0, r.y0 + size), text, **kw)

    def edit(self, page_no, rect, new_text, fontfile=None, size=11, color=(0, 0, 0),
             erase_old=True):
        """แก้ทั้งกรอบ: ลบข้อความทุกชั้นในกรอบ (ถ้าเลือก) แล้ววางข้อความใหม่"""
        self._snapshot()
        page = self.doc[page_no]
        r = self._to_page_rect(page, rect)
        try:
            if erase_old:
                self.erase(page, r)
            if new_text.strip():
                self.write(page, r, new_text, fontfile, size, color)
        except Exception:
            self.undo()                           # แก้ไม่สำเร็จ → ย้อนกลับ
            raise

    # ── แก้ข้อความ: ทีละชิ้น (เลือกชั้นได้) ──────────────────────────
    def _erase_span(self, page_no, span):
        """ลบเฉพาะข้อความชิ้นที่เลือก ข้อความชั้นอื่นที่ซ้อนอยู่ตรงเดียวกันจะถูกวางคืน"""
        page = self.doc[page_no]
        bb = fitz.Rect(span["bbox"])
        horizontal = abs(span["dir"][0]) > 0.99
        # หดกรอบแนวตั้งให้เหลือแถบกลางบรรทัด จะได้ไม่โดนบรรทัดบน/ล่าง
        r = fitz.Rect(bb.x0 + 0.2, bb.y0 + bb.height * 0.3, bb.x1 - 0.2, bb.y1 - bb.height * 0.3) \
            if horizontal else fitz.Rect(bb)
        if r.is_empty:
            r = bb

        others, fonts, skipped = [], {}, False
        for s in page.get_texttrace():
            if not skipped and s["seqno"] == span["seqno"] and tuple(s["bbox"]) == span["bbox"]:
                skipped = True
                continue
            if not fitz.Rect(s["bbox"]).intersects(bb):
                continue
            others.append(s)
            if s["font"] not in fonts:             # ต้องหาฟอนต์ก่อนลบ เพราะลบแล้วหน้าอาจไม่อ้างถึงฟอนต์นั้นอีก
                txt = "".join(chr(c[0]) for c in s["chars"])
                fonts[s["font"]] = self.resolve_font(page_no, s["font"], txt)[0] or find_default_font()

        self._redact(page, [r], images=0, graphics=0)

        # หาตัวอักษรของชั้นอื่นที่หายไปด้วย แล้วคืนฟังก์ชันสำหรับวางคืน
        left = collections.Counter(_ckey(c) for s in page.get_texttrace() for c in s["chars"])
        jobs = []
        for s in others:
            lost = []
            for c in s["chars"]:
                k = _ckey(c)
                if left[k] > 0:
                    left[k] -= 1
                else:
                    lost.append(c)
            path = fonts.get(s["font"])
            if not lost or not path:
                continue
            jobs.append((s, lost, self._font(path)))

        def restore(above):
            """วางตัวอักษรที่หายคืน ณ ตำแหน่งเดิม: above=False ชั้นที่อยู่ใต้ชิ้นที่แก้, True ชั้นที่อยู่เหนือ"""
            for s, lost, font in jobs:
                if (s["seqno"] > span["seqno"]) != above:
                    continue
                tw = fitz.TextWriter(page.rect, opacity=s.get("opacity", 1.0), color=_rgb(s["color"]))
                n = 0
                for c in lost:
                    if not chr(c[0]).isspace() and font.has_glyph(c[0]):
                        tw.append(c[2], chr(c[0]), font=font, fontsize=s["size"])
                        n += 1
                rm = s.get("type", 0)
                if n:
                    tw.write_text(page, render_mode=rm if rm in (0, 1, 2, 3) else 0)
        return restore

    def _write_at(self, page, span, text, fontfile, size, color):
        """วางข้อความใหม่ให้เส้นฐาน (baseline) และจุดเริ่มตรงกับข้อความเดิม"""
        horizontal = span["dir"][0] > 0.99 and abs(span["dir"][1]) < 0.01
        if not (horizontal and page.rotation == 0 and hasattr(page, "insert_htmlbox")):
            bb = fitz.Rect(span["bbox"])
            self.write(page, bb + (-1, -1, 1, 1), text, fontfile, size, color)
            return
        body, css, archive = self._html(text, fontfile, size, color, pre=True)
        # ลองวางบนหน้าชั่วคราวก่อน เพื่อวัดว่าเส้นฐานของบรรทัดแรกตกตรงไหนของกรอบ
        w, h, m = 4000, size * 1.3 * (text.count("\n") + 1) + size * 2, 50
        tmp = fitz.open()
        tp = tmp.new_page(width=w + 2 * m, height=h + 2 * m)
        tp.insert_htmlbox(fitz.Rect(m, m, m + w, m + h), body, css=css, archive=archive)
        tr = [s for s in tp.get_texttrace() if s["chars"]]
        tmp.close()
        if not tr:
            raise RuntimeError("ฟอนต์นี้วางข้อความไม่ได้")
        ox, oy = tr[0]["chars"][0][2]
        x, y = span["origin"]
        x0, y0 = x - (ox - m), y - (oy - m)
        page.insert_htmlbox(fitz.Rect(x0, y0, x0 + w, y0 + h), body, css=css, archive=archive)

    def edit_span(self, page_no, span, new_text, fontfile=None, size=None, color=None,
                  erase_old=True):
        """แก้ข้อความชิ้นเดียว (span จาก spans_at) โดยไม่แตะข้อความชั้นอื่น"""
        self._snapshot()
        try:
            restore = self._erase_span(page_no, span) if erase_old else (lambda above: None)
            restore(False)                         # ชั้นล่างก่อน → ข้อความใหม่ → ชั้นบน เพื่อคงลำดับชั้นเดิม
            if new_text.strip():
                self._write_at(self.doc[page_no], span, new_text, fontfile,
                               size or span["size"], color or span["color"])
            restore(True)
        except Exception:
            self.undo()
            raise

    # ── เส้นตาราง ────────────────────────────────────────────────────
    @staticmethod
    def _norm_items(d):
        """รายการวาดของ path: แปลง quad สี่เหลี่ยมเป็น rect และเติมเส้นปิด path ให้ครบ"""
        items = []
        for it in d["items"]:
            if it[0] == "qu" and it[1].is_rectangular and abs(it[1].ul.y - it[1].ur.y) < 0.01:
                it = ("re", it[1].rect, 1)
            items.append(it)
        if d.get("closePath") and items and all(i[0] == "l" for i in items):
            if abs(items[-1][2] - items[0][1]) > 0.01:
                items.append(("l", items[-1][2], items[0][1]))
        return items

    def _segments(self, drawings):
        segs = []

        def add(pi, ii, ei, kind, p1, p2, width, color):
            if abs(p1.y - p2.y) <= 0.5 and abs(p1.x - p2.x) > 0.5:
                o, pos, a, b = "h", (p1.y + p2.y) / 2, min(p1.x, p2.x), max(p1.x, p2.x)
            elif abs(p1.x - p2.x) <= 0.5 and abs(p1.y - p2.y) > 0.5:
                o, pos, a, b = "v", (p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)
            else:
                return
            segs.append(dict(id=(pi, ii, ei), kind=kind, o=o, pos=pos, a=a, b=b,
                             width=width or 1.0, color=_rgb(color)))

        for pi, d in enumerate(drawings):
            stroke = "s" in d["type"] and d.get("color") is not None
            fill = "f" in d["type"] and d.get("fill") is not None
            for ii, it in enumerate(self._norm_items(d)):
                if it[0] == "l" and stroke:
                    add(pi, ii, 0, "l", it[1], it[2], d.get("width"), d["color"])
                elif it[0] == "re":
                    r = fitz.Rect(it[1])
                    r.normalize()
                    if stroke:                     # กรอบสี่เหลี่ยม = 4 เส้น
                        for ei, (p1, p2) in enumerate([(r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)]):
                            add(pi, ii, ei, "edge", p1, p2, d.get("width"), d["color"])
                    elif fill and min(r.width, r.height) <= 3 and max(r.width, r.height) >= 3 * min(r.width, r.height):
                        # สี่เหลี่ยมทึบบาง ๆ = เส้น (โปรแกรมหลายตัววาดเส้นตารางแบบนี้)
                        if r.width >= r.height:
                            y = (r.y0 + r.y1) / 2
                            add(pi, ii, 0, "thin", fitz.Point(r.x0, y), fitz.Point(r.x1, y), r.height, d["fill"])
                        else:
                            x = (r.x0 + r.x1) / 2
                            add(pi, ii, 0, "thin", fitz.Point(x, r.y0), fitz.Point(x, r.y1), r.width, d["fill"])
        return segs

    def segments(self, page_no):
        """เส้นตรงแนวนอน/แนวตั้งทั้งหมดบนหน้า (พิกัดจริงของหน้า)"""
        return self._segments(self.doc[page_no].get_drawings())

    @staticmethod
    def line_group(segs, seg):
        """เส้นที่อยู่แนวเดียวกันและต่อเนื่องกับ seg (ตารางมักวาดเส้นยาวเป็นท่อนสั้น ๆ ต่อกัน)"""
        same = [s for s in segs if s["o"] == seg["o"] and abs(s["pos"] - seg["pos"]) <= 0.75]
        group, lo, hi, grew = [seg], seg["a"], seg["b"], True
        while grew:
            grew = False
            for s in same:
                if s not in group and s["a"] <= hi + 1 and s["b"] >= lo - 1:
                    group.append(s)
                    lo, hi, grew = min(lo, s["a"]), max(hi, s["b"]), True
        return group

    @staticmethod
    def _apply_change(s, c):
        if c.get("delete"):
            return None
        n = dict(s, pos=s["pos"] + c.get("dpos", 0), a=s["a"] + c.get("da", 0), b=s["b"] + c.get("db", 0))
        if c.get("width"):
            n["width"] = c["width"]
        if c.get("color") is not None:
            n["color"] = c["color"]
        return n if n["b"] - n["a"] > 0.1 else None

    @staticmethod
    def _draw_path(shape, items, d, fill, stroke):
        for it in items:
            if it[0] == "l":
                shape.draw_line(it[1], it[2])
            elif it[0] == "re":
                shape.draw_rect(it[1])
            elif it[0] == "qu":
                shape.draw_quad(it[1])
            elif it[0] == "c":
                shape.draw_bezier(it[1], it[2], it[3], it[4])
        cap = d.get("lineCap") or 0
        fo, so = d.get("fill_opacity"), d.get("stroke_opacity")
        shape.finish(fill=d.get("fill") if fill else None, color=d.get("color") if stroke else None,
                     width=d.get("width") or 1, dashes=d.get("dashes") or None,
                     even_odd=bool(d.get("even_odd")), closePath=False,
                     lineJoin=d.get("lineJoin") or 0, lineCap=max(cap) if isinstance(cap, tuple) else cap,
                     fill_opacity=1 if fo is None else fo, stroke_opacity=1 if so is None else so)

    def edit_lines(self, page_no, seg_ids, delta=0.0, width=None, color=None, delete=False,
                   stretch=True):
        """ย้าย (delta หน่วย pt ตั้งฉากกับเส้น) / เปลี่ยนความหนา-สี / ลบ เส้นที่ระบุ

        stretch=True: เส้นตั้งฉากที่ปลายมาชนเส้นนี้จะยืด/หดตาม (เหมือนปรับขนาดแถว/คอลัมน์)

        วิธีทำ: PDF ไม่มีคำสั่ง "ย้ายเส้น" จึงต้องลบลายเส้นเดิมออกแล้ววาดใหม่
        """
        self._snapshot()
        try:
            page = self.doc[page_no]
            drawings = page.get_drawings()
            segs = self._segments(drawings)
            byid = {s["id"]: s for s in segs}
            targets = [byid[i] for i in seg_ids if i in byid]
            if not targets:
                raise RuntimeError("ไม่พบเส้นที่เลือก")
            changes = {t["id"]: dict(dpos=delta, width=width, color=color, delete=delete) for t in targets}
            if stretch and delta and not delete:
                for q in segs:
                    if q["id"] in changes or q["o"] == targets[0]["o"]:
                        continue
                    for t in targets:
                        tol = 1.0 + (t["width"] + q["width"]) / 2
                        if not (t["a"] - tol <= q["pos"] <= t["b"] + tol):
                            continue
                        if abs(q["a"] - t["pos"]) <= tol:
                            changes[q["id"]] = dict(da=delta)
                        elif abs(q["b"] - t["pos"]) <= tol:
                            changes[q["id"]] = dict(db=delta)
                        else:
                            continue
                        break

            # ลายเส้นที่ต้องลบแล้ววาดใหม่ = path ที่มีเส้นเป้าหมาย + ทุกอย่างที่อยู่ในกรอบของมัน
            # + พื้นสีที่อยู่ใต้พื้นสีที่ถูกลบ (ต้องวาดใหม่ด้วยเพื่อรักษาลำดับชั้น)
            def pad(d):
                w = 3 + (d.get("width") or 0)
                return fitz.Rect(d["rect"]) + (-w, -w, w, w)

            def is_area(d):
                r = d["rect"]
                return "f" in d["type"] and d.get("fill") is not None and min(r.width, r.height) > 3

            vict = {i[0] for i in changes}
            while True:
                rects = [pad(drawings[i]) for i in vict]
                new = set(vict)
                for i, d in enumerate(drawings):
                    if i not in new and any(fitz.Rect(d["rect"]) in r for r in rects):
                        new.add(i)
                for i in list(new):
                    if is_area(drawings[i]):
                        for g in range(i):
                            if g not in new and is_area(drawings[g]) and \
                                    fitz.Rect(drawings[g]["rect"]).intersects(drawings[i]["rect"]):
                                new.add(g)
                if new == vict:
                    break
                vict = new

            self._redact(page, [pad(drawings[i]) for i in vict], images=0, graphics=1, text=1)

            left = collections.Counter(_dkey(d) for d in page.get_drawings())
            removed = []
            for i, d in enumerate(drawings):
                k = _dkey(d)
                if left[k] > 0:
                    left[k] -= 1
                else:
                    removed.append(i)
            if not {i[0] for i in changes} <= set(removed):
                raise RuntimeError("ลบเส้นเดิมออกจากหน้าไม่ได้ (ลายเส้นแบบนี้ยังไม่รองรับ)")

            by_item = collections.defaultdict(list)
            for s in segs:
                by_item[s["id"][:2]].append(s)

            under, over = page.new_shape(), page.new_shape()   # พื้นสีไว้ล่างสุด เส้นไว้บนสุด
            n_under = n_over = 0
            new_lines = []                                     # (seg, lineCap, dashes)
            for pi in removed:
                d = drawings[pi]
                keep, fills = [], []
                for ii, it in enumerate(self._norm_items(d)):
                    ss = by_item.get((pi, ii), [])
                    if not any(s["id"] in changes for s in ss):
                        keep.append(it)
                        continue
                    if ss[0]["kind"] == "edge":                # แตกกรอบสี่เหลี่ยมเป็น 4 เส้น
                        if "f" in d["type"] and d.get("fill") is not None:
                            fills.append(it)
                        cap = 2
                    else:
                        cap = d.get("lineCap") or 0
                        cap = max(cap) if isinstance(cap, tuple) else cap
                    for s in ss:
                        n = self._apply_change(s, changes[s["id"]]) if s["id"] in changes else s
                        if n:
                            new_lines.append((n, cap, d.get("dashes") or None))
                has_fill = "f" in d["type"] and d.get("fill") is not None
                has_stroke = "s" in d["type"] and d.get("color") is not None
                if keep and has_fill:
                    if is_area(d):
                        self._draw_path(under, keep, d, True, False)
                        n_under += 1
                    else:
                        self._draw_path(over, keep, d, True, False)
                        n_over += 1
                if keep and has_stroke:
                    self._draw_path(over, keep, d, False, True)
                    n_over += 1
                if fills:
                    self._draw_path(under, fills, d, True, False)
                    n_under += 1
            for n, cap, dashes in new_lines:
                if n["kind"] == "thin":                        # เส้นแบบสี่เหลี่ยมทึบ → วาดเป็นสี่เหลี่ยมทึบตามเดิม
                    w = n["width"] / 2
                    r = fitz.Rect(n["a"], n["pos"] - w, n["b"], n["pos"] + w) if n["o"] == "h" \
                        else fitz.Rect(n["pos"] - w, n["a"], n["pos"] + w, n["b"])
                    over.draw_rect(r)
                    over.finish(fill=n["color"], color=None)
                else:
                    p1, p2 = self.seg_points(n)
                    over.draw_line(p1, p2)
                    over.finish(color=n["color"], width=n["width"], lineCap=cap, dashes=dashes, closePath=False)
                n_over += 1
            if n_under:
                under.commit(overlay=False)
            if n_over:
                over.commit(overlay=True)
        except Exception:
            self.undo()
            raise

    @staticmethod
    def seg_points(s):
        if s["o"] == "h":
            return fitz.Point(s["a"], s["pos"]), fitz.Point(s["b"], s["pos"])
        return fitz.Point(s["pos"], s["a"]), fitz.Point(s["pos"], s["b"])

    def add_line(self, page_no, p1, p2, width=1.0, color=(0, 0, 0)):
        """วาดเส้นใหม่ (พิกัดจริงของหน้า)"""
        self._snapshot()
        try:
            shape = self.doc[page_no].new_shape()
            shape.draw_line(fitz.Point(p1), fitz.Point(p2))
            shape.finish(color=color, width=width, closePath=False)
            shape.commit()
        except Exception:
            self.undo()
            raise


# ════════════════════════════════════════════════════════════════════
# ส่วนที่ 3: หน้าต่างโปรแกรม
# ════════════════════════════════════════════════════════════════════
class EditorApp(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.title("PDF Editor")
        self.geometry("1280x820")

        self.core = PdfCore()
        self.path = None
        self.page_no = 0
        self.zoom = 1.5
        self._img = None           # ต้องเก็บอ้างอิงไว้ ไม่งั้นภาพหาย
        self._drag_start = None
        self._new_end = None

        # โหมดข้อความ
        self.sel = None            # กรอบที่ลาก (พิกัดตามที่เห็น ไม่คูณซูม) หรือ None ถ้าแค่คลิก
        self.spans = []            # ข้อความที่พบใต้จุดคลิก/ในกรอบ เรียงจากชั้นบนสุด
        self.cur_span = None       # ชิ้นที่เลือกจะแก้ (None = ทั้งกรอบ)
        self.fontfile = find_default_font()
        self.color = (0, 0, 0)

        # โหมดเส้น
        self.segs = []             # เส้นทั้งหมดของหน้านี้
        self.line_sel = []         # เส้นที่เลือก
        self.line_color = (0, 0, 0)
        self._line_drag = None     # (จุดเริ่มในพิกัดหน้า, ระยะที่ลากแล้ว)

        self._build_ui()
        if path:
            self.load(path)

    # ── สร้างหน้าจอ ──────────────────────────────────────────────────
    def _build_ui(self):
        bar = ttk.Frame(self, padding=4)
        bar.pack(side="top", fill="x")
        for label, cmd in [("เปิด", self.ask_open), ("บันทึกเป็น…", self.ask_save),
                           ("◀", lambda: self.goto(self.page_no - 1)),
                           ("▶", lambda: self.goto(self.page_no + 1)),
                           ("−", lambda: self.set_zoom(self.zoom / 1.25)),
                           ("+", lambda: self.set_zoom(self.zoom * 1.25)),
                           ("ย้อนกลับ", self.undo)]:
            ttk.Button(bar, text=label, command=cmd).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.mode = tk.StringVar(value="text")
        ttk.Label(bar, text="โหมด:").pack(side="left")
        for label, val in [("ข้อความ", "text"), ("เส้นตาราง", "line")]:
            ttk.Radiobutton(bar, text=label, value=val, variable=self.mode,
                            command=self._mode_changed).pack(side="left", padx=4)
        self.status = ttk.Label(bar, text="ยังไม่ได้เปิดไฟล์")
        self.status.pack(side="left", padx=10)

        self.msg_label = ttk.Label(self, text="", padding=4, foreground="#333")
        self.msg_label.pack(side="bottom", fill="x")

        side = ttk.Frame(self, padding=8, width=340)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        self.text_panel = ttk.Frame(side)
        self.line_panel = ttk.Frame(side)
        self._build_text_panel(self.text_panel)
        self._build_line_panel(self.line_panel)
        self.text_panel.pack(fill="both", expand=True)

        # พื้นที่แสดงหน้า PDF
        area = ttk.Frame(self)
        area.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(area, bg="#777", cursor="crosshair", highlightthickness=0)
        vs = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(area, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
        for key, d in [("<Left>", (-1, 0)), ("<Right>", (1, 0)), ("<Up>", (0, -1)), ("<Down>", (0, 1))]:
            self.canvas.bind(key, lambda e, d=d: self.nudge_line(*d))
        self.bind("<Control-z>", lambda e: self.undo())
        if sys.platform == "darwin":               # ปุ่ม Command มีเฉพาะบน Mac (ระบบอื่น bind แล้ว error)
            self.bind("<Command-z>", lambda e: self.undo())

    def _build_text_panel(self, side):
        ttk.Label(side, text="ข้อความที่พบ (บนสุดของรายการ = ชั้นบนสุด)").pack(anchor="w")
        self.span_list = tk.Listbox(side, height=7, exportselection=False, activestyle="none")
        self.span_list.pack(fill="x", pady=(0, 8))
        self.span_list.bind("<<ListboxSelect>>", self.on_pick_span)

        ttk.Label(side, text="ข้อความเดิม").pack(anchor="w")
        self.old_box = tk.Text(side, height=4, wrap="word", state="disabled", bg="#f0f0f0")
        self.old_box.pack(fill="x", pady=(0, 8))

        ttk.Label(side, text="ข้อความใหม่").pack(anchor="w")
        self.new_box = tk.Text(side, height=6, wrap="word")
        self.new_box.pack(fill="x", pady=(0, 8))

        row = ttk.Frame(side)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="ขนาด").pack(side="left")
        self.size_var = tk.DoubleVar(value=11)
        ttk.Spinbox(row, from_=4, to=96, increment=0.5, width=6,
                    textvariable=self.size_var).pack(side="left", padx=4)
        self.color_sw = tk.Label(row, width=3, bg="#000000", relief="solid", borderwidth=1)
        self.color_sw.pack(side="left", padx=(8, 2))
        ttk.Button(row, text="สี…", width=4, command=self.pick_color).pack(side="left")

        self.auto_font = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="ใช้ฟอนต์เดิมของข้อความ (หาให้อัตโนมัติ)",
                        variable=self.auto_font, command=self._show_font).pack(anchor="w", pady=(8, 0))
        ttk.Button(side, text="เลือกฟอนต์เอง (.ttf/.otf)…", command=self.pick_font).pack(fill="x", pady=2)
        self.font_label = ttk.Label(side, wraplength=320, foreground="#555")
        self.font_label.pack(anchor="w")

        self.erase_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="ลบข้อความเดิม", variable=self.erase_var).pack(anchor="w", pady=6)
        ttk.Button(side, text="ใช้การแก้ไข", command=self.apply).pack(fill="x", pady=4)
        ttk.Label(side, wraplength=320, foreground="#555",
                  text="วิธีใช้: คลิกที่ข้อความ (หรือลากกรอบ) → เลือกชิ้นที่ต้องการในรายการ "
                       "→ พิมพ์ข้อความใหม่ → ใช้การแก้ไข\n"
                       "ถ้าข้อความซ้อนกัน รายการจะแสดงทุกชั้น เลือกแก้ได้ทีละชั้น\n"
                       "เว้นข้อความใหม่ว่างไว้ = ลบอย่างเดียว").pack(anchor="w", pady=8)
        self._show_font()

    def _build_line_panel(self, side):
        ttk.Label(side, text="เส้นที่เลือก").pack(anchor="w")
        self.line_info = ttk.Label(side, text="ยังไม่ได้เลือกเส้น", wraplength=320, foreground="#555")
        self.line_info.pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(side)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="ความหนา (pt)").pack(side="left")
        self.lw_var = tk.DoubleVar(value=1.0)
        ttk.Spinbox(row, from_=0.1, to=20, increment=0.25, width=6,
                    textvariable=self.lw_var).pack(side="left", padx=4)
        self.lcolor_sw = tk.Label(row, width=3, bg="#000000", relief="solid", borderwidth=1)
        self.lcolor_sw.pack(side="left", padx=(8, 2))
        ttk.Button(row, text="สี…", width=4, command=self.pick_line_color).pack(side="left")

        ttk.Button(side, text="ใช้ความหนา/สีนี้กับเส้นที่เลือก", command=self.apply_line_style).pack(fill="x", pady=(8, 2))
        ttk.Button(side, text="ลบเส้นที่เลือก", command=self.delete_line).pack(fill="x", pady=2)

        self.group_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="เลือกทั้งแนวเส้น (ท่อนที่ต่อกัน)", variable=self.group_var).pack(anchor="w", pady=(10, 0))
        self.stretch_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="เส้นที่มาชน ยืด/หดตามเมื่อย้าย", variable=self.stretch_var).pack(anchor="w")
        self.add_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text="เพิ่มเส้นใหม่ (ลากบนหน้า)", variable=self.add_var,
                        command=self._clear_line_sel).pack(anchor="w", pady=(10, 0))

        ttk.Label(side, wraplength=320, foreground="#555",
                  text="วิธีใช้:\n• คลิกที่เส้นเพื่อเลือก (เส้นที่แก้ได้แสดงเป็นสีฟ้า)\n"
                       "• ลากเส้นที่เลือกเพื่อย้าย หรือกดปุ่มลูกศรเพื่อขยับทีละ 0.5 pt\n"
                       "• ปรับความหนา/สี แล้วกดปุ่มใช้\n"
                       "• ติ๊ก 'เพิ่มเส้นใหม่' แล้วลากเพื่อวาดเส้นตรง\n\n"
                       "แก้ได้เฉพาะเส้นที่เป็นลายเส้นจริงใน PDF "
                       "ถ้าตารางเป็นรูปภาพ/ไฟล์สแกนจะเลือกเส้นไม่ได้").pack(anchor="w", pady=10)

    def msg(self, text):
        self.msg_label.config(text=text)

    def _mode_changed(self):
        self.text_panel.pack_forget()
        self.line_panel.pack_forget()
        (self.text_panel if self.mode.get() == "text" else self.line_panel).pack(fill="both", expand=True)
        self._clear_text_sel()
        self.line_sel = []
        self._update_line_info()
        self._draw_overlay()
        if self.mode.get() == "line" and self.core.doc:
            self.msg("พบเส้นที่แก้ได้ %d เส้นในหน้านี้" % len(self.segs) if self.segs
                     else "หน้านี้ไม่มีลายเส้นที่แก้ได้ (ตารางอาจเป็นรูปภาพ) — ยังเพิ่มเส้นใหม่ได้")
        else:
            self.msg("")

    # ── ไฟล์ ─────────────────────────────────────────────────────────
    def ask_open(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if path:
            self.load(path)

    def load(self, path):
        try:
            self.core.open(path)
        except Exception as e:
            messagebox.showerror("เปิดไฟล์ไม่ได้", str(e))
            return
        self.path = path
        self.page_no = 0
        self._clear_text_sel()
        self.line_sel = []
        self.refresh()

    def ask_save(self):
        if not self.core.doc:
            return
        base, ext = os.path.splitext(os.path.basename(self.path))
        out = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           initialfile=base + "_edited.pdf",
                                           filetypes=[("PDF", "*.pdf")])
        if out:
            try:
                self.core.save(out)
                messagebox.showinfo("บันทึกแล้ว", out)
            except Exception as e:
                messagebox.showerror("บันทึกไม่ได้", str(e))

    # ── แสดงหน้า ─────────────────────────────────────────────────────
    def refresh(self):
        if not self.core.doc:
            return
        data, w, h = self.core.render(self.page_no, self.zoom)
        self._img = tk.PhotoImage(data=data)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._img)
        self.canvas.configure(scrollregion=(0, 0, w, h))
        try:
            self.segs = self.core.segments(self.page_no)
        except Exception:
            self.segs = []
        self._draw_overlay()
        self.status.config(text="หน้า %d / %d   ซูม %d%%" %
                           (self.page_no + 1, self.core.page_count, self.zoom * 100))

    def goto(self, n):
        if self.core.doc and 0 <= n < self.core.page_count:
            self.page_no = n
            self._clear_text_sel()
            self.line_sel = []
            self._update_line_info()
            self.refresh()

    def set_zoom(self, z):
        self.zoom = max(0.5, min(z, 5))
        self.refresh()

    # ── พิกัด ────────────────────────────────────────────────────────
    def _xy(self, e):
        # พิกัดบน canvas (รวมระยะที่เลื่อน scrollbar แล้ว)
        return self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)

    def _page_pt(self, cx, cy):
        """จุดบน canvas → พิกัดจริงของหน้า"""
        return self.core.from_view(self.page_no, cx / self.zoom, cy / self.zoom)

    def _canvas_pt(self, x, y):
        """พิกัดจริงของหน้า → จุดบน canvas"""
        vx, vy = self.core.to_view(self.page_no, x, y)
        return vx * self.zoom, vy * self.zoom

    # ── วาดทับบนหน้า (กรอบเลือก / ไฮไลต์) ────────────────────────────
    def _draw_overlay(self):
        c = self.canvas
        c.delete("ov")
        if not self.core.doc:
            return
        z = self.zoom
        if self.mode.get() == "text":
            if self.sel:
                x0, y0, x1, y1 = self.sel
                c.create_rectangle(x0 * z, y0 * z, x1 * z, y1 * z,
                                   outline="#e11", width=2, dash=(4, 2), tags="ov")
            for s in self.spans:
                x0, y0, x1, y1 = s["bbox"]
                a, b = self._canvas_pt(x0, y0), self._canvas_pt(x1, y1)
                cur = s is self.cur_span
                c.create_rectangle(a[0], a[1], b[0], b[1], outline="#06c" if cur else "#9bd",
                                   width=2 if cur else 1, tags="ov")
        else:
            sel_ids = {s["id"] for s in self.line_sel}
            shift = self._line_drag[1] if self._line_drag else 0
            for s in self.segs:
                picked = s["id"] in sel_ids
                p1, p2 = PdfCore.seg_points(dict(s, pos=s["pos"] + shift) if picked else s)
                a, b = self._canvas_pt(p1.x, p1.y), self._canvas_pt(p2.x, p2.y)
                c.create_line(a[0], a[1], b[0], b[1], fill="#e11" if picked else "#39f",
                              width=3 if picked else 1, tags="ov")

    # ── เมาส์ ────────────────────────────────────────────────────────
    def on_press(self, e):
        if not self.core.doc:
            return
        self.canvas.focus_set()
        self._drag_start = self._xy(e)
        if self.mode.get() == "line" and not self.add_var.get():
            self._press_line()

    def on_drag(self, e):
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._xy(e)
        if self.mode.get() == "text":
            z = self.zoom
            self.spans, self.cur_span = [], None
            self.sel = (min(x0, x1) / z, min(y0, y1) / z, max(x0, x1) / z, max(y0, y1) / z)
            self._draw_overlay()
        elif self.add_var.get():
            if abs(x1 - x0) >= abs(y1 - y0):       # บังคับให้เป็นเส้นตรงแนวนอน/แนวตั้ง
                y1 = y0
            else:
                x1 = x0
            self.canvas.delete("new")
            self.canvas.create_line(x0, y0, x1, y1, fill="#e11", width=2, tags="new")
            self._new_end = (x1, y1)
        elif self._line_drag and self.line_sel:
            start = self._line_drag[0]
            px, py = self._page_pt(x1, y1)
            d = (py - start[1]) if self.line_sel[0]["o"] == "h" else (px - start[0])
            self._line_drag = (start, d)
            self._draw_overlay()

    def on_release(self, e):
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._xy(e)
        self._drag_start = None
        if self.mode.get() == "text":
            self._release_text(x0, y0, x1, y1)
        elif self.add_var.get():
            self.canvas.delete("new")
            end, self._new_end = self._new_end, None
            if end and max(abs(end[0] - x0), abs(end[1] - y0)) > 4:
                self._run(lambda: self.core.add_line(self.page_no, self._page_pt(x0, y0), self._page_pt(*end),
                                                     width=float(self.lw_var.get()), color=self.line_color),
                          "เพิ่มเส้นแล้ว")
        elif self._line_drag:
            d = self._line_drag[1]
            self._line_drag = None
            if abs(d) > 0.3 and self.line_sel:
                self._move_line(d)
            else:
                self._draw_overlay()

    # ── โหมดข้อความ ──────────────────────────────────────────────────
    def _clear_text_sel(self):
        self.sel, self.spans, self.cur_span = None, [], None
        self.span_list.delete(0, "end")
        self._set_old("")
        self._show_font()

    def _release_text(self, x0, y0, x1, y1):
        z = self.zoom
        if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:  # คลิก = หาข้อความใต้จุดนั้น
            self.sel = None
            rect = (x0 / z - 1, y0 / z - 1, x0 / z + 1, y0 / z + 1)
        else:
            self.sel = rect = (min(x0, x1) / z, min(y0, y1) / z, max(x0, x1) / z, max(y0, y1) / z)
        self.spans = self.core.spans_at(self.page_no, rect)
        self.span_list.delete(0, "end")
        if self.sel:
            self.span_list.insert("end", "▣ ทั้งกรอบ — ทุกชั้นรวมกัน / วางข้อความใหม่ในกรอบ")
        for i, s in enumerate(self.spans):
            tag = "ชั้น %d" % (len(self.spans) - i) if len(self.spans) > 1 else "ข้อความ"
            extra = (" [มองไม่เห็น]" if s["hidden"] else "") + (" [เลเยอร์ %s]" % s["layer"] if s["layer"] else "")
            txt = s["text"].strip()
            self.span_list.insert("end", "%s%s: %s — %s %gpt" % (
                tag, extra, txt if len(txt) <= 26 else txt[:25] + "…", s["font"], s["size"]))
        if not self.spans and not self.sel:
            self._clear_text_sel()
            self._draw_overlay()
            self.msg("ไม่พบข้อความตรงนั้น — ถ้าเป็นรูปภาพ/ไฟล์สแกนจะแก้ข้อความไม่ได้ (ลากกรอบเพื่อวางข้อความใหม่ได้)")
            return
        # ค่าเริ่มต้น: คลิก → ชั้นบนสุด, ลากกรอบ → ชิ้นนั้นถ้ามีชิ้นเดียว ไม่งั้นทั้งกรอบ
        first = 0 if not self.sel else (1 if len(self.spans) == 1 else 0)
        self.span_list.selection_set(first)
        self.on_pick_span()
        self.msg("พบข้อความ %d ชิ้นในบริเวณนี้ — เลือกชิ้นที่จะแก้จากรายการด้านขวา" % len(self.spans)
                 if len(self.spans) > 1 else "")
        self.new_box.focus_set()

    def on_pick_span(self, _=None):
        cur = self.span_list.curselection()
        if not cur:
            return
        i = cur[0] - (1 if self.sel else 0)
        if i < 0:                                  # ทั้งกรอบ
            self.cur_span = None
            text, size, color = self.core.inspect(self.page_no, self.sel)
        else:
            self.cur_span = s = self.spans[i]
            text, size, color = s["text"], s["size"], s["color"]
        self._set_old(text)
        self.new_box.delete("1.0", "end")
        self.new_box.insert("1.0", text)           # เติมข้อความเดิมให้แก้ต่อได้เลย
        self.size_var.set(round(size, 1))
        self._set_color(color)
        self._show_font()
        self._draw_overlay()

    def _set_old(self, text):
        self.old_box.config(state="normal")
        self.old_box.delete("1.0", "end")
        self.old_box.insert("1.0", text)
        self.old_box.config(state="disabled")

    def _set_color(self, rgb):
        self.color = rgb
        self.color_sw.config(bg="#%02x%02x%02x" % tuple(int(c * 255) for c in rgb))

    def pick_color(self):
        rgb, _ = colorchooser.askcolor()
        if rgb:
            self._set_color(tuple(c / 255 for c in rgb))

    def pick_font(self):
        p = filedialog.askopenfilename(filetypes=[("Font", "*.ttf *.otf")])
        if p:
            self.fontfile = p
            self.auto_font.set(False)
            self._show_font()

    def _orig_font(self):
        """ชื่อฟอนต์เดิมของข้อความที่กำลังจะแก้"""
        if self.cur_span:
            return self.cur_span["font"]
        return self.spans[0]["font"] if self.spans else None

    def _pick_fontfile(self, text):
        """คืน (ไฟล์ฟอนต์ที่จะใช้, คำอธิบาย)"""
        fallback = os.path.basename(self.fontfile) if self.fontfile else "ไม่มี"
        name = self._orig_font()
        if not self.auto_font.get() or not name or not self.core.doc:
            return self.fontfile, "ฟอนต์: " + fallback
        path, src = self.core.resolve_font(self.page_no, name, text)
        if src == "system":
            return path, "ฟอนต์เดิม: %s ✓ มีในเครื่อง" % name
        if src == "embedded":
            return path, "ฟอนต์เดิม: %s ✓ ใช้ฟอนต์ที่ฝังในไฟล์" % name
        return self.fontfile, ("ฟอนต์เดิม: %s ✗ ไม่มีในเครื่อง และฟอนต์ที่ฝังในไฟล์มีตัวอักษรไม่ครบ "
                               "→ ใช้ %s แทน (ติดตั้งฟอนต์ %s ในเครื่องแล้วจะเหมือนเดิม)" % (name, fallback, name))

    def _show_font(self):
        self.font_label.config(text=self._pick_fontfile(self.new_box.get("1.0", "end-1c"))[1])

    def apply(self):
        if not self.cur_span and not self.sel:
            messagebox.showinfo("ยังไม่ได้เลือก", "คลิกที่ข้อความ หรือลากกรอบบริเวณที่ต้องการแก้ก่อน")
            return
        text = self.new_box.get("1.0", "end-1c")
        fontfile, note = self._pick_fontfile(text)
        kw = dict(fontfile=fontfile, size=float(self.size_var.get()), color=self.color,
                  erase_old=self.erase_var.get())
        try:
            if self.cur_span:
                self.core.edit_span(self.page_no, self.cur_span, text, **kw)
            else:
                self.core.edit(self.page_no, self.sel, text, **kw)
            self.msg("แก้แล้ว — " + note)
        except Exception as e:
            messagebox.showerror("แก้ไขไม่สำเร็จ", str(e))
        self._clear_text_sel()
        self.refresh()

    # ── โหมดเส้น ─────────────────────────────────────────────────────
    def _clear_line_sel(self):
        self.line_sel = []
        self._update_line_info()
        self._draw_overlay()

    def _hit_seg(self, cx, cy):
        px, py = self._page_pt(cx, cy)
        best, best_d = None, None
        for s in self.segs:
            along, across = (px, py) if s["o"] == "h" else (py, px)
            if not (s["a"] - 2 <= along <= s["b"] + 2):
                continue
            d = abs(across - s["pos"])
            if d <= 5 / self.zoom + s["width"] / 2 and (best is None or d < best_d):
                best, best_d = s, d
        return best, (px, py)

    def _press_line(self):
        s, pt = self._hit_seg(*self._drag_start)
        if not s:
            self._line_drag = None
            self._clear_line_sel()
            return
        if s["id"] not in {x["id"] for x in self.line_sel}:
            self.line_sel = PdfCore.line_group(self.segs, s) if self.group_var.get() else [s]
            self.lw_var.set(round(s["width"], 2))
            self._set_line_color(s["color"])
        self._line_drag = (pt, 0.0)
        self._update_line_info()
        self._draw_overlay()

    def _update_line_info(self):
        if not self.line_sel:
            self.line_info.config(text="ยังไม่ได้เลือกเส้น")
            return
        s = self.line_sel[0]
        lo, hi = min(x["a"] for x in self.line_sel), max(x["b"] for x in self.line_sel)
        self.line_info.config(text="เส้น%s ยาว %.0f pt หนา %.2f pt (%d ท่อน)" % (
            "แนวนอน" if s["o"] == "h" else "แนวตั้ง", hi - lo, s["width"], len(self.line_sel)))

    def _set_line_color(self, rgb):
        self.line_color = rgb
        self.lcolor_sw.config(bg="#%02x%02x%02x" % tuple(int(c * 255) for c in rgb))

    def pick_line_color(self):
        rgb, _ = colorchooser.askcolor()
        if rgb:
            self._set_line_color(tuple(c / 255 for c in rgb))

    def _run(self, fn, done=""):
        """ทำคำสั่งแก้ไข แล้ววาดหน้าใหม่"""
        try:
            fn()
            self.msg(done)
            ok = True
        except Exception as e:
            messagebox.showerror("แก้ไขไม่สำเร็จ", str(e))
            ok = False
        self.refresh()
        return ok

    def _reselect(self, o, pos, a, b):
        """หลังแก้ รหัสเส้นเปลี่ยน → หาเส้นเดิมจากตำแหน่งใหม่เพื่อคงการเลือกไว้"""
        for s in self.segs:
            if s["o"] == o and abs(s["pos"] - pos) <= 0.6 and s["a"] <= b and s["b"] >= a:
                self.line_sel = PdfCore.line_group(self.segs, s) if self.group_var.get() else [s]
                break
        else:
            self.line_sel = []
        self._update_line_info()
        self._draw_overlay()

    def _line_edit(self, done, **kw):
        if not self.line_sel:
            self.msg("คลิกเลือกเส้นก่อน")
            return
        s = self.line_sel[0]
        o, pos = s["o"], s["pos"] + kw.get("delta", 0)
        a, b = min(x["a"] for x in self.line_sel), max(x["b"] for x in self.line_sel)
        ids = [x["id"] for x in self.line_sel]
        self.line_sel = []
        if self._run(lambda: self.core.edit_lines(self.page_no, ids, stretch=self.stretch_var.get(), **kw), done) \
                and not kw.get("delete"):
            self._reselect(o, pos, a, b)
        else:
            self._update_line_info()

    def _move_line(self, d):
        self._line_edit("ย้ายเส้น %.1f pt" % d, delta=d)

    def nudge_line(self, dx, dy):
        if self.mode.get() != "line" or not self.line_sel:
            return
        # ลูกศรอ้างทิศบนจอ → แปลงเป็นทิศในพิกัดจริงของหน้า (เผื่อหน้าถูกหมุน)
        x0, y0 = self.core.from_view(self.page_no, 0, 0)
        x1, y1 = self.core.from_view(self.page_no, dx, dy)
        d = (y1 - y0) if self.line_sel[0]["o"] == "h" else (x1 - x0)
        if abs(d) > 0.5:
            self._move_line(0.5 if d > 0 else -0.5)

    def apply_line_style(self):
        self._line_edit("เปลี่ยนความหนา/สีแล้ว", width=float(self.lw_var.get()), color=self.line_color)

    def delete_line(self):
        self._line_edit("ลบเส้นแล้ว", delete=True)

    # ── ย้อนกลับ ─────────────────────────────────────────────────────
    def undo(self):
        if self.core.undo():
            self._clear_text_sel()
            self.line_sel = []
            self._update_line_info()
            self.refresh()
            self.msg("ย้อนกลับแล้ว")


if __name__ == "__main__":
    EditorApp(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
