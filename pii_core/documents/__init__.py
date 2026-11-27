"""Pipeline tài liệu: extract -> mask -> render -> leak-check.

Tách 2 bước để sau này chèn bước review (P5):
    segs = extract(data, "a.docx")                      # Segment (JSON hoá được)
    res  = mask_segments(segs, analyze, policy)          # replacements + mapping
    out  = render(data, "a.docx", res.replacements)      # file đã che
Hoặc gộp: mask_document(data, "a.docx", analyze, policy)
Segment id ổn định: trích lại từ cùng file gốc luôn ra cùng id, nên render chỉ cần file gốc + replacements.

PDF scan: truyền `ocr` (pii_core.ocr.LightOnOcr). Kết quả OCR trả về trong MaskedDocument.ocr_pages - phải lưu lại
(intermediate/, mã hoá) để render lần sau dùng StoredOcr(ocr_pages), KHÔNG OCR lại (tốn GPU, text có thể khác).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

from .. import leakcheck
from ..engine import AnalyzeFn
from ..policy import Policy
from ..segments import MaskResult, Replacement, Segment, mask_segments
from .docx_handler import DocxDocument, DocxError
from .json_payload import JsonPayloadDocument, JsonPayloadError
from .pdf_handler import PdfDocument, PdfError

SUPPORTED = {".docx", ".json", ".pdf"}
_HANDLERS = {".docx": DocxDocument, ".json": JsonPayloadDocument, ".pdf": PdfDocument}


class UnsupportedFormat(ValueError):
    pass


def _kind(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFormat(f"chưa hỗ trợ định dạng '{ext}' (hỗ trợ: {', '.join(sorted(SUPPORTED))})")
    return ext


def _open(data: bytes, filename: str, ocr=None, progress=None):
    kind = _kind(filename)
    if kind == ".pdf":
        return PdfDocument(data, ocr=ocr, progress=progress)
    return _HANDLERS[kind](data)


def extract(data: bytes, filename: str, ocr=None) -> list[Segment]:
    return _open(data, filename, ocr).segments()


def render(data: bytes, filename: str, replacements: list[Replacement], ocr=None) -> bytes:
    doc = _open(data, filename, ocr)
    doc.apply(replacements)
    return doc.save()


@dataclass
class MaskedDocument:
    data: bytes
    result: MaskResult
    leak: leakcheck.LeakReport
    segments: list[Segment]
    ocr_pages: dict = field(default_factory=dict)   # {số trang: markdown OCR gốc} - CHỨA PII, lưu mã hoá

    @property
    def status(self) -> str:
        return "done" if self.leak.clean else "needs_review"


def verify(out: bytes, filename: str, mapping, analyze: AnalyzeFn | None, policy, ignore=()) -> leakcheck.LeakReport:
    doc = _open(out, filename)
    return leakcheck.check(mapping, doc.raw_text_nodes(), doc.segments(), analyze, policy, doc.warnings,
                           getattr(doc, "unprocessed", []), ignore)


def rerender(data: bytes, filename: str, replacements: list[Replacement], mapping, analyze: AnalyzeFn, policy,
             ocr=None, ignore=()) -> tuple[bytes, leakcheck.LeakReport]:
    """Xuất lại file từ replacements đã sửa (sau review) + leak-check. `ocr` = StoredOcr (không OCR lại)."""
    doc = _open(data, filename, ocr)
    doc.apply(replacements)
    out = doc.save()
    leak = verify(out, filename, mapping, analyze, policy, ignore)
    leak.warnings = doc.warnings + [w for w in leak.warnings if w not in doc.warnings]
    return out, leak


def mask_document(data: bytes, filename: str, analyze: AnalyzeFn, policy: Policy | str | None = None,
                  recheck: bool = True, ocr=None, progress=None) -> MaskedDocument:
    doc = _open(data, filename, ocr, progress)
    segs = doc.segments()
    res = mask_segments(segs, analyze, policy)
    doc.apply(res.replacements)
    out = doc.save()
    leak = verify(out, filename, res.mapping, analyze if recheck else None, policy)
    # cảnh báo của bản gốc (vd "đã dựng lại trang") + của output, bỏ trùng
    leak.warnings = doc.warnings + [w for w in leak.warnings if w not in doc.warnings]
    ocr_pages = {p: mt.md for p, mt in getattr(doc, "ocr_pages", {}).items()}
    return MaskedDocument(out, res, leak, segs, ocr_pages)


__all__ = ["DocxDocument", "DocxError", "JsonPayloadDocument", "JsonPayloadError", "PdfDocument", "PdfError",
           "MaskedDocument", "UnsupportedFormat", "extract", "mask_document",
           "render", "rerender", "verify", "SUPPORTED"]
