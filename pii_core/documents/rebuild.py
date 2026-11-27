"""Dựng lại trang PDF từ markdown OCR ĐÃ CHE (dự phòng khi không che được trên ảnh scan).

markdown -> HTML (thư viện `markdown`, bảng HTML của OCR giữ nguyên) -> pymupdf.Story -> trang PDF cùng khổ
với trang gốc. Nội dung dài hơn 1 trang thì sinh thêm trang. Mỗi trang có dòng ghi chú "dựng lại từ OCR"
để người đọc biết đây không phải ảnh gốc.

Font: cần font Unicode có dấu tiếng Việt. Thư mục font lấy từ PII_FONT_DIR, mặc định C:\\Windows\\Fonts
(Docker: cài fonts-dejavu / fonts-noto và đặt PII_FONT_DIR). Tên file: PII_FONT_REGULAR / PII_FONT_BOLD.
"""
from __future__ import annotations

import html
import io
import os
from pathlib import Path

import markdown as md_lib
import pymupdf

from ..pseudonymizer import Pseudonymizer

MARGIN = 42
NOTE = "Trang {page}: dựng lại từ OCR (bản scan gốc đã được thay để che thông tin cá nhân)"
CSS = """
@font-face {{ font-family: vf; src: url({regular}); }}
@font-face {{ font-family: vf; src: url({bold}); font-weight: bold; }}
body {{ font-family: vf; font-size: 9.5pt; line-height: 1.25; }}
h1 {{ font-size: 13pt; }} h2 {{ font-size: 12pt; }} h3, h4, h5, h6 {{ font-size: 10.5pt; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border: 0.5px solid #999; padding: 1.5px 3px; font-size: 8.5pt; vertical-align: top; }}
p.note {{ font-size: 7pt; color: #888; }}
"""


def _font_dir() -> tuple[Path, str, str]:
    d = Path(os.environ.get("PII_FONT_DIR", r"C:\Windows\Fonts"))
    reg = os.environ.get("PII_FONT_REGULAR", "arial.ttf")
    bold = os.environ.get("PII_FONT_BOLD", "arialbd.ttf")
    if not (d / reg).exists():
        raise FileNotFoundError(f"không có font {d / reg} - đặt PII_FONT_DIR / PII_FONT_REGULAR")
    if not (d / bold).exists():
        bold = reg
    return d, reg, bold


def markdown_to_html(masked_md: str) -> str:
    # tag <PERSON_1> sẽ bị hiểu là thẻ HTML -> escape trước khi chuyển
    safe = Pseudonymizer.TAG_RE.sub(lambda m: html.escape(m.group(0)), masked_md)
    # nl2br: giữ ngắt dòng của OCR (dòng chữ ký, địa chỉ nhiều dòng) thay vì dồn thành một đoạn
    return md_lib.markdown(safe, extensions=["tables", "nl2br"], output_format="html")


def render_pages(pages: list[tuple[int, str]], rect: pymupdf.Rect | None = None) -> list[bytes]:
    """[(số trang gốc, markdown đã che)] -> mỗi trang gốc một PDF (có thể nhiều trang)."""
    font_dir, reg, bold = _font_dir()
    archive = pymupdf.Archive(str(font_dir))
    css = CSS.format(regular=reg, bold=bold)
    out = []
    for pno, masked_md in pages:
        mediabox = pymupdf.Rect(rect) if rect else pymupdf.paper_rect("a4")
        where = mediabox + (MARGIN, MARGIN, -MARGIN, -MARGIN)
        body = f'<p class="note">{html.escape(NOTE.format(page=pno))}</p>' + markdown_to_html(masked_md)
        story = pymupdf.Story(html=body, user_css=css, archive=archive)
        buf = io.BytesIO()
        writer = pymupdf.DocumentWriter(buf)
        more = 1
        while more:
            dev = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
        writer.close()
        out.append(buf.getvalue())
    return out
