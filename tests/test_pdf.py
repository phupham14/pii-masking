"""Test PDF handler. PDF mẫu dựng trong test (PyMuPDF + Arial của Windows để có dấu tiếng Việt); dữ liệu hư cấu."""
from pathlib import Path

import pymupdf
import pytest

from pii_core.documents import PdfDocument, PdfError, mask_document
from pii_core.documents.pdf_handler import _garbled

FONT = Path(r"C:\Windows\Fonts\arial.ttf")
pytestmark = pytest.mark.skipif(not FONT.exists(), reason="cần font Arial để dựng PDF tiếng Việt")

BODY = ("Hợp đồng ký với Ông Nguyễn Văn An, CCCD số 079185012345, thường trú tại số 12 Đường Lê Lợi, "
        "Phường Bến Thành, Quận 1. Điện thoại 0912 345 678.")
MUST_GO = ["Nguyễn Văn An", "079185012345", "0912 345 678", "Lê Lợi", "an.nguyen@example.com", "Trần Thị Bích"]


def _page(doc, text, y=72, width=440, fontsize=11):
    page = doc.new_page()
    page.insert_font(fontname="F0", fontfile=str(FONT))
    page.insert_textbox(pymupdf.Rect(72, y, 72 + width, y + 300), text, fontname="F0", fontsize=fontsize)
    return page


def build_pdf() -> bytes:
    doc = pymupdf.open()
    p = _page(doc, BODY)
    p.insert_text((72, 400), "Email: an.nguyen@example.com", fontname="F0", fontsize=11)
    p.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 390, 300, 405),
                   "uri": "mailto:an.nguyen@example.com"})
    p.add_text_annot((400, 400), "Ghi chú: kiểm tra CCCD của Trần Thị Bích")
    # tên vắt qua 2 dòng (khung hẹp)
    _page(doc, "Người lập biểu báo cáo này là Bà Trần Thị Bích, kế toán trưởng.", width=170)
    doc.set_metadata({"author": "Trần Thị Bích", "title": "Hồ sơ Nguyễn Văn An"})
    doc.set_toc([[1, "Hồ sơ Ông Nguyễn Văn An", 1]])
    return doc.tobytes()


def build_scans() -> bytes:
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 280), False)
    pix.set_rect(pix.irect, (240, 240, 240))
    p1 = doc.new_page()
    p1.insert_image(p1.rect, pixmap=pix)                                   # scan thuần
    p2 = doc.new_page()
    p2.insert_image(p2.rect, pixmap=pix)                                   # scan + lớp OCR ẩn
    p2.insert_font(fontname="F0", fontfile=str(FONT))
    p2.insert_textbox(pymupdf.Rect(72, 72, 500, 400), "Ong Tran Hung Huy - Chu tich " * 5, fontname="F0",
                      render_mode=3)
    _page(doc, BODY)                                                       # trang text bình thường
    return doc.tobytes()


def all_text(data: bytes) -> str:
    d = pymupdf.open(stream=data, filetype="pdf")
    parts = [pg.get_text() for pg in d] + [str(v) for v in d.metadata.values()] + [t[1] for t in d.get_toc()]
    parts += [ln.get("uri", "") for pg in d for ln in pg.get_links()]
    parts += [a.info.get("content", "") for pg in d for a in (pg.annots() or [])]
    return "\n".join(parts)


@pytest.fixture(scope="module")
def sample():
    return build_pdf()


@pytest.fixture(scope="module")
def masked(sample, analyze):
    return mask_document(sample, "hoso.pdf", analyze, "bctc")


def test_source_contains_pii(sample):
    text = " ".join(all_text(sample).split())
    for v in MUST_GO:
        assert v in text, v


def test_pii_gone_from_output(masked):
    text = " ".join(all_text(masked.data).split())
    for v in MUST_GO:
        assert v not in text, v
    assert masked.status == "done", masked.leak.to_dict()


def test_neighbouring_text_kept(masked):
    text = " ".join(all_text(masked.data).split())
    for v in ["Hợp đồng ký với Ông", "CCCD số", "thường trú tại", "Điện thoại", "kế toán trưởng"]:
        assert v in text, v
    # địa chỉ cá nhân vắt 2 dòng: tag phải hiện (đặt ở ô rộng nhất), không mất vì ô đầu quá hẹp
    assert "<ADDRESS_1>" in text


def test_name_wrapped_over_two_lines(sample, masked):
    src = pymupdf.open(stream=sample, filetype="pdf")[1].get_text()
    assert "Trần Thị\nBích" in src or "Trần\nThị Bích" in src or "Thị Bích" not in src.split("\n")[0]
    assert "Bích" not in pymupdf.open(stream=masked.data, filetype="pdf")[1].get_text()


def test_metadata_annots_links_toc(masked):
    d = pymupdf.open(stream=masked.data, filetype="pdf")
    assert not any(d.metadata.get(k) for k in ("author", "title"))
    assert not list(d[0].annots() or [])
    assert not [ln for ln in d[0].get_links() if "mailto" in ln.get("uri", "")]
    assert d.get_toc()[0][1] == "Hồ sơ Ông <PERSON_1>"


def test_scan_pages_are_never_clean(analyze):
    r = mask_document(build_scans(), "scan.pdf", analyze)
    kinds = {u["page"]: u["kind"] for u in r.leak.unprocessed}
    assert kinds == {1: "scan", 2: "scan_ocr_layer"}
    assert r.status == "needs_review"
    assert "Nguyễn Văn An" not in pymupdf.open(stream=r.data, filetype="pdf")[2].get_text()  # trang text vẫn được che


def test_garbled_detection():
    tcvn3 = "C«ng ty cæ phÇn s÷a ViÖt Nam, ®Þa chØ: sè 10 T©n Trµo, quËn 7, thµnh phè Hå ChÝ Minh " * 2
    assert _garbled(tcvn3)
    assert not _garbled(BODY * 2)


def test_rejects_bad_input():
    with pytest.raises(PdfError):
        PdfDocument(b"not a pdf")
