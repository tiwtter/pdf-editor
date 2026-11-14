#!/bin/bash
# macOS / Linux: ดับเบิลคลิก (Mac) หรือรัน ./run.command
# ครั้งแรกจะสร้าง .venv และติดตั้ง PyMuPDF ให้เอง
cd "$(dirname "$0")" || exit 1
PY=python3
command -v $PY >/dev/null 2>&1 || { echo "ไม่พบ python3 — ติดตั้งจาก https://www.python.org/downloads/"; read -r; exit 1; }
if [ ! -x .venv/bin/python ]; then
    echo "ติดตั้งครั้งแรก…"
    $PY -m venv .venv && .venv/bin/python -m pip install -q --upgrade pip && \
        .venv/bin/python -m pip install -q -r requirements.txt || { echo "ติดตั้งไม่สำเร็จ"; read -r; exit 1; }
fi
exec .venv/bin/python pdf_editor.py "$@"
