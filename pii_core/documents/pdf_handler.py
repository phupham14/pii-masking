"""PDF có lớp text: trích text + toạ độ từng ký tự (PyMuPDF rawdict) -> Segment -> redact thật trên PDF gốc.

- Segment = 1 block text (PyMuPDF block ~ paragraph). Dòng bị ngắt do hết chiều rộng (wrap) nối bằng " " để tên /
  địa chỉ vắt dòng vẫn nhận được; dòng kết thúc sớm (dòng nhãn "Họ tên: ...", ô bảng) nối bằng "\n".
  Ký tự nối không có toạ độ.
- Che: mỗi span -> các rect (1 rect / dòng) -> add_redact_annot(ô xám, tag ở rect đầu) -> apply_redactions:
  xoá text dưới ô (kể cả text ẩn của lớp OCR), xoá pixel ảnh dưới ô, GIỮ đường kẻ bảng.
- Phân loại trang: "text" / "scan" (ảnh, gần như không có chữ) / "scan_ocr_layer" (ảnh phủ kín trang + lớp text
  OCR sẵn: chất lượng không kiểm soát được, không tin) / "garbled" (font TCVN3/VNI giải mã ra rác).
  Trang không phải "text" CHƯA xử lý ở P2 -> `unprocessed`, file không bao giờ được coi là sạch (cần OCR - P3).
- Dọn: metadata + XMP, annotation, file đính kèm, form field, link mailto:/tel: và link chứa giá trị bị che,
  mục lục (bookmark) đi qua analyzer như segment.
- Lưu: ghi lại toàn bộ file (garbage=4, clean) - không lưu incremental, nên nội dung cũ không còn trong file.

Giấy phép: PyMuPDF là AGPL-3.0 (hoặc license thương mại Artifex). Mọi chỗ dùng PyMuPDF nằm trong file này.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from typing import TYPE_CHECKING

from ..segments import Replacement, Segment

if TYPE_CHECKING:
    from .markdown_text import MarkdownText  # noqa: F401

MAX_PAGES = 2000
SCAN_MIN_CHARS = 30                      # ít hơn số ký tự này mà có ảnh -> coi là trang scan
SCAN_IMAGE_COVERAGE = 0.5                # ảnh phủ ≥ 50% trang -> bản scan (kể cả khi có lớp text OCR sẵn)
GARBLED_RATIO = 0.04                     # tỉ lệ ký tự "lạ" trong chữ cái vượt ngưỡng -> font TCVN3/VNI
# Ký tự Latin-1 mà tiếng Việt Unicode dùng; các ký tự U+00A0-U+00FF còn lại hay là rác của TCVN3/VNI
VI_LATIN1 = set("àáâãèéêìíòóôõùúýÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝ")
TAG_FILL = (0.85, 0.85, 0.85)
VERTICAL_SHRINK = 0.2                    # thu ô che theo chiều dọc để không ăn sang dòng trên/dưới


class PdfError(ValueError):
    pass


@dataclass
class _Char:
    c: str
    bbox: tuple | None                   # None = ký tự nối (không có trên trang)
    line: int = -1


@dataclass
class _Block:
    page: int
    chars: list[_Char] = field(default_factory=list)

    @property
    def text(self):
        return "".join(ch.c for ch in self.chars)


def _image_coverage(page) -> float:
    """Tổng diện tích ảnh (cắt theo trang) / diện tích trang. PDF số hoá: ≤ ~0.2; bản scan: ≥ ~0.7."""
    area = abs(page.rect) or 1.0
    return sum(abs(pymupdf.Rect(im["bbox"]) & page.rect) for im in page.get_image_info()) / area


def _garbled(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 40:
        return False
    odd = sum(1 for c in letters if (" " <= c <= "ÿ" and c not in VI_LATIN1)
              or "" <= c <= "") + text.count("�")
    return odd / len(letters) > GARBLED_RATIO


class PdfDocument:
    def __init__(self, data: bytes, ocr=None, progress=None):
        """ocr: OcrEngine (pii_core.ocr) để xử lý trang scan / text rác; None = để các trang đó ở `unprocessed`."""
        self._data = data
        try:
            self.doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:  # noqa: BLE001 - MuPDF ném nhiều loại lỗi
            raise PdfError(f"không mở được PDF: {e}") from e
        if self.doc.needs_pass:
            raise PdfError("PDF có mật khẩu: không xử lý được")
        if self.doc.page_count > MAX_PAGES:
            raise PdfError(f"PDF quá dài ({self.doc.page_count} trang)")
        self.warnings: list[str] = []
        self.page_kinds: list[str] = []
        self.unprocessed: list[dict] = []
        self._blocks: dict[str, _Block] = {}
        self._toc = self.doc.get_toc(simple=False)
        self._toc_seg: dict[str, int] = {}
        self.ocr_pages: dict[int, "MarkdownText"] = {}     # số trang (1-based) -> text OCR (markdown)
        self._scrub()
        self._collect()
        if ocr is not None and self.unprocessed:
            self._run_ocr(ocr, progress)

    @classmethod
    def open(cls, path) -> "PdfDocument":
        with open(path, "rb") as f:
            return cls(f.read())

    # ------------------------------------------------------------------ dọn
    def _scrub(self):
        self.doc.set_metadata({})
        self.doc.del_xml_metadata()
        n_emb = self.doc.embfile_count()
        for name in list(self.doc.embfile_names()):
            self.doc.embfile_del(name)
        if n_emb:
            self.warnings.append(f"đã xoá {n_emb} file đính kèm trong PDF")
        n_annot = n_widget = 0
        for page in self.doc:
            for a in list(page.annots() or []):
                page.delete_annot(a)
                n_annot += 1
            for w in list(page.widgets() or []):
                page.delete_widget(w)
                n_widget += 1
        if n_annot:
            self.warnings.append(f"đã xoá {n_annot} annotation (ghi chú, highlight...)")
        if n_widget:
            self.warnings.append(f"đã xoá {n_widget} ô form (form field)")

    # ------------------------------------------------------------------ trích
    def _collect(self):
        flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP
        for pno, page in enumerate(self.doc):
            raw = page.get_text("rawdict", flags=flags)
            blocks = [b for b in raw["blocks"] if b.get("type") == 0]
            page_text = "".join(ch["c"] for b in blocks for ln in b["lines"] for sp in ln["spans"] for ch in sp["chars"])
            coverage = _image_coverage(page)
            if coverage >= SCAN_IMAGE_COVERAGE:
                # Ảnh phủ gần kín trang = bản scan. Nếu có lớp text thì đó là OCR có sẵn, chất lượng không kiểm soát
                # được (vd BCTC ACB 2024: tên mất dấu "Ong Tran Hung Huy" -> luật không bắt được, còn ảnh vẫn hiện tên)
                kind = "scan_ocr_layer" if len(page_text.strip()) >= SCAN_MIN_CHARS else "scan"
            elif len(page_text.strip()) < SCAN_MIN_CHARS and coverage > 0:
                kind = "scan"
            elif _garbled(page_text):
                kind = "garbled"
            else:
                kind = "text"
            self.page_kinds.append(kind)
            if kind != "text":
                self.unprocessed.append(dict(page=pno + 1, kind=kind))
                continue
            for bi, b in enumerate(blocks):
                blk = _Block(pno)
                x1, prev_line_x1 = b["bbox"][2], 0.0
                for li, ln in enumerate(b["lines"]):
                    chars = [ch for sp in ln["spans"] for ch in sp["chars"]]
                    if not chars:
                        continue
                    if blk.chars:
                        avg_w = sum(c["bbox"][2] - c["bbox"][0] for c in chars) / len(chars)
                        prev_end = blk.chars[-1]
                        wrapped = prev_line_x1 >= x1 - 3 * max(avg_w, 1)
                        if prev_end.c == "-" and wrapped:
                            pass                           # gạch nối cuối dòng: nối thẳng
                        else:
                            blk.chars.append(_Char(" " if wrapped else "\n", None))
                    for ch in chars:
                        blk.chars.append(_Char(ch["c"], tuple(ch["bbox"]), li))
                    prev_line_x1 = max(c["bbox"][2] for c in chars)
                if blk.text.strip():
                    self._blocks[f"p{pno + 1}#b{bi}"] = blk
        if self.unprocessed:
            kinds = {}
            for u in self.unprocessed:
                kinds.setdefault(u["kind"], []).append(u["page"])
            labels = {"scan": "trang scan (ảnh)", "scan_ocr_layer": "trang scan có lớp OCR sẵn (không tin được)",
                      "garbled": "trang có font TCVN3/VNI (text rác)"}
            for k, pages in kinds.items():
                self.warnings.append(f"{len(pages)} {labels[k]} CHƯA được che, cần OCR: {_ranges(pages)}")

    def _run_ocr(self, ocr, progress):
        from ..ocr import OcrError
        from .markdown_text import MarkdownText
        pages = [u["page"] for u in self.unprocessed]
        try:
            results = ocr.ocr_pdf(self._data, pages, progress=progress)
        except OcrError as e:
            self.warnings.append(f"OCR lỗi, {len(pages)} trang vẫn CHƯA được che: {e}")
            return
        got = {r.page: r for r in results if (r.markdown or "").strip()}
        for p in pages:
            if p in got:
                self.ocr_pages[p] = MarkdownText(got[p].markdown)
        done = set(self.ocr_pages)
        self.unprocessed = [u for u in self.unprocessed if u["page"] not in done]
        self.warnings = [w for w in self.warnings if "CHƯA được che, cần OCR" not in w]
        if done:
            self.warnings.append(f"{len(done)} trang scan được OCR rồi DỰNG LẠI (không còn ảnh gốc): {_ranges(sorted(done))}")
        if self.unprocessed:
            self.warnings.append(f"{len(self.unprocessed)} trang OCR không trả kết quả / không có chữ, CHƯA được che: "
                                 f"{_ranges([u['page'] for u in self.unprocessed])}")

    def segments(self) -> list[Segment]:
        out = [Segment(sid, b.text, dict(kind="pdf", page=b.page + 1)) for sid, b in self._blocks.items()]
        out += [Segment(f"p{p}#ocr", mt.text, dict(kind="ocr", page=p)) for p, mt in sorted(self.ocr_pages.items())
                if mt.text.strip()]
        for i, entry in enumerate(self._toc):
            if len(entry) > 1 and str(entry[1]).strip():
                sid = f"toc#{i}"
                self._toc_seg[sid] = i
                out.append(Segment(sid, entry[1], dict(kind="toc")))
        return out

    # ------------------------------------------------------------------ che
    def apply(self, replacements: list[Replacement]):
        by_seg: dict[str, list[Replacement]] = {}
        for r in replacements:
            by_seg.setdefault(r.seg, []).append(r)
        pages_touched, values = set(), set()
        for sid, reps in by_seg.items():
            if sid.endswith("#ocr"):
                continue                                      # trang OCR: xử lý ở _replace_ocr_pages
            if sid in self._toc_seg:
                i = self._toc_seg[sid]
                title = self._toc[i][1]
                values.update(title[r.start:r.end] for r in reps)
                for r in sorted(reps, key=lambda r: r.start, reverse=True):
                    title = title[:r.start] + r.tag + title[r.end:]
                self._toc[i][1] = title
                continue
            blk = self._blocks[sid]
            page = self.doc[blk.page]
            values.update(blk.text[r.start:r.end] for r in reps)
            for r in reps:
                rects = self._rects(blk, r.start, r.end)
                widest = max(range(len(rects)), key=lambda k: rects[k].width, default=0)
                for k, rect in enumerate(rects):
                    tag = r.tag if k == widest else None   # span vắt dòng: ghi tag vào ô rộng nhất
                    fs = self._fit_fontsize(rect, tag) if tag else 11
                    page.add_redact_annot(rect, text=tag if tag and fs else None, fontname="helv",
                                          fontsize=fs or 11, fill=TAG_FILL, text_color=(0, 0, 0), cross_out=False)
                pages_touched.add(blk.page)
        for pno in pages_touched:
            self.doc[pno].apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                           graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                           text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if self._toc_seg:
            self.doc.set_toc(self._toc)
        for p, mt in self.ocr_pages.items():
            values.update(mt.text[r.start:r.end] for r in by_seg.get(f"p{p}#ocr", []))
        self._drop_pii_links(values)
        self._replace_ocr_pages(by_seg)

    def _replace_ocr_pages(self, by_seg):
        """Thay mỗi trang scan đã OCR bằng trang dựng lại từ markdown đã che (từ trang cuối lên để giữ số trang)."""
        if not self.ocr_pages:
            return
        from .rebuild import render_pages
        toc = self.doc.get_toc(simple=False)
        for p in sorted(self.ocr_pages, reverse=True):
            mt = self.ocr_pages[p]
            masked = mt.mask(by_seg.get(f"p{p}#ocr", []))
            rect = self.doc[p - 1].rect
            new = pymupdf.open("pdf", render_pages([(p, masked)], rect)[0])
            self.doc.delete_page(p - 1)
            self.doc.insert_pdf(new, start_at=p - 1)
        if toc:
            try:
                self.doc.set_toc(toc)
            except ValueError:
                self.warnings.append("mục lục không gắn lại được sau khi dựng lại trang")

    @staticmethod
    def _rects(blk: _Block, start: int, end: int):
        """Rect bao các ký tự thật trong [start, end), gộp theo dòng; thu chiều dọc để không chạm dòng kề."""
        lines: dict[int, list] = {}
        for ch in blk.chars[start:end]:
            if ch.bbox is not None and not ch.c.isspace():
                lines.setdefault(ch.line, []).append(ch.bbox)
        rects = []
        for boxes in lines.values():
            x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            h = y1 - y0
            rects.append(pymupdf.Rect(x0, y0 + h * VERTICAL_SHRINK / 2, x1, y1 - h * VERTICAL_SHRINK / 2))
        return rects

    @staticmethod
    def _fit_fontsize(rect, tag):
        """Cỡ chữ lớn nhất (≤ 9pt) để tag nằm gọn trong ô; 0 nếu ô quá nhỏ (ô vẫn được che, không có chữ)."""
        width = pymupdf.get_text_length(tag, fontname="helv", fontsize=1)
        fs = min(9.0, rect.width / max(width, 1e-6), rect.height * 0.9)
        return fs if fs >= 3 else 0

    def _drop_pii_links(self, values: set[str]):
        """Xoá link mailto:/tel: và link có chứa giá trị vừa bị che (vd URL có email / số định danh)."""
        squeeze = lambda s: re.sub(r"\s+", "", s).lower()  # noqa: E731
        vals = {squeeze(v) for v in values if len(squeeze(v)) >= 4}
        for page in self.doc:
            for ln in page.get_links():
                uri = ln.get("uri") or ""
                if uri.lower().startswith(("mailto:", "tel:")) or any(v in squeeze(uri) for v in vals):
                    page.delete_link(ln)

    # ------------------------------------------------------------------ lưu + kiểm tra
    def save(self) -> bytes:
        return self.doc.tobytes(garbage=4, deflate=True, clean=True)

    def raw_text_nodes(self):
        for pno, page in enumerate(self.doc):
            t = page.get_text()
            if t.strip():
                yield f"page{pno + 1}", t
            for ln in page.get_links():
                if ln.get("uri"):
                    yield f"page{pno + 1}/link", ln["uri"]
        for k, v in (self.doc.metadata or {}).items():
            if v:
                yield f"metadata/{k}", v
        for i, entry in enumerate(self.doc.get_toc()):
            yield f"toc/{i}", entry[1]


def _ranges(pages: list[int]) -> str:
    out, start = [], None
    for i, p in enumerate(pages):
        if start is None:
            start = p
        if i + 1 == len(pages) or pages[i + 1] != p + 1:
            out.append(f"{start}" if start == p else f"{start}-{p}")
            start = None
    return ", ".join(out)
