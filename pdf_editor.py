#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF Editor ต้นแบบ — แก้ข้อความทีละชั้น และปรับเส้นตารางในไฟล์ PDF

ติดตั้ง:   pip install pymupdf
รัน:       python pdf_editor.py [ไฟล์.pdf]

โครงสร้าง
  pdf_core.py  — FontIndex + PdfCore ตรรกะแก้ PDF ล้วน ๆ (ใช้ร่วมกับเวอร์ชันเว็บใน web/)
  ไฟล์นี้      — EditorApp หน้าต่าง tkinter มี 2 โหมด: ข้อความ / เส้นตาราง
"""
import os
import sys
try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk
except ImportError:
    sys.exit("Python นี้ไม่มี tkinter — Linux: sudo apt install python3-tk | "
             "macOS (Homebrew): brew install python-tk")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_core import PdfCore, find_default_font, fitz  # noqa: E402,F401


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
