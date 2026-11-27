"""P3: trang PDF scan -> OCR -> che -> dựng lại trang. OCR giả lập (không gọi mạng); dữ liệu hư cấu."""
from pathlib import Path

import pymupdf
import pytest

from pii_core.documents import mask_document, render
from pii_core.ocr import LightOnOcr, OcrError, OcrPage, StoredOcr

FONT = Path(r"C:\Windows\Fonts\arial.ttf")
pytestmark = pytest.mark.skipif(not FONT.exists(), reason="cần font Arial để dựng trang tiếng Việt")

OCR_MD = {
    1: "# BÁO CÁO\n**Ông** Nguyễn Văn An - Tổng Giám đốc\n<table><tr><td>Kiểm toán viên</td><td>Trần Thị Bích</td>"
       "</tr><tr><td>Số Giấy CN ĐKHN</td><td>1234-2023-001-1</td></tr></table>\nĐiện thoại: 0912 345 678",
    2: "Trụ sở: số 111A, Đường Pasteur, Phường Bến Nghé, Quận 1\nNgười ký: Nguyễn Văn An",
}


class FakeOcr:
    def __init__(self, pages=OCR_MD, fail=False):
        self.pages, self.fail, self.calls = pages, fail, []

    def ocr_pdf(self, data, pages, progress=None):
        self.calls.append(list(pages))
        if self.fail:
            raise OcrError("gateway không phản hồi")
        return [OcrPage(p, self.pages[p]) for p in pages if p in self.pages]


def scan_pdf(n_scan=2, with_text_page=True) -> bytes:
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 140), False)
    pix.set_rect(pix.irect, (235, 235, 235))
    for _ in range(n_scan):
        p = doc.new_page()
        p.insert_image(p.rect, pixmap=pix)
    if with_text_page:
        p = doc.new_page()
        p.insert_font(fontname="F0", fontfile=str(FONT))
        p.insert_text((72, 100), "Phụ lục: liên hệ Ông Lê Văn Tám, ĐT 0988 777 666", fontname="F0", fontsize=11)
    return doc.tobytes()


def page_texts(data):
    return [" ".join(pg.get_text().split()) for pg in pymupdf.open("pdf", data)]


@pytest.fixture(scope="module")
def masked(analyze):
    return mask_document(scan_pdf(), "scan.pdf", analyze, ocr=FakeOcr())


def test_scan_pages_rebuilt_and_masked(masked):
    texts = page_texts(masked.data)
    assert len(texts) == 3                                    # mỗi trang ngắn -> 1 trang dựng lại, giữ số trang
    joined = " ".join(texts)
    for v in ("Nguyễn Văn An", "Trần Thị Bích", "1234-2023-001-1", "0912 345 678", "Lê Văn Tám", "0988 777 666"):
        assert v not in joined, v
    assert "dựng lại từ OCR" in texts[0] and "Kiểm toán viên" in texts[0]
    assert "Đường Pasteur" in texts[1]                         # địa chỉ tổ chức: giữ
    assert masked.status == "done", masked.leak.to_dict()


def test_same_person_same_tag_across_ocr_pages(masked):
    texts = page_texts(masked.data)
    tag = {m["original"]: m["tag"] for m in masked.result.mapping}["Nguyễn Văn An"]
    assert tag in texts[0] and tag in texts[1]


def test_ocr_pages_kept_for_rerender(analyze, masked):
    """render() sau review dùng OCR đã lưu, không gọi OCR lại."""
    assert set(masked.ocr_pages) == {1, 2}
    out = render(scan_pdf(), "scan.pdf", masked.result.replacements, ocr=StoredOcr(masked.ocr_pages))
    assert page_texts(out) == page_texts(masked.data)


def test_ocr_failure_leaves_pages_unprocessed(analyze):
    r = mask_document(scan_pdf(), "scan.pdf", analyze, ocr=FakeOcr(fail=True))
    assert [u["page"] for u in r.leak.unprocessed] == [1, 2]
    assert r.status == "needs_review"
    assert any("OCR lỗi" in w for w in r.leak.warnings)
    assert "Lê Văn Tám" not in page_texts(r.data)[2]           # trang text vẫn được che


def test_only_scan_pages_sent_to_ocr(analyze):
    fake = FakeOcr()
    mask_document(scan_pdf(), "scan.pdf", analyze, ocr=fake)
    assert fake.calls == [[1, 2]]


def test_lighton_always_sends_privacy_flags(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def fake_request(method, url, **kw):
        if url.endswith("/v1/ocr/async"):
            sent.update(kw["data"])
            return Resp({"job_id": "j1"})
        if url.endswith("/v1/ocr/jobs/j1"):
            return Resp({"status": "completed"})
        return Resp({"pages": [{"page": 1, "markdown": "x"}]})

    ocr = LightOnOcr(url="http://gw", key="k", poll=0)
    monkeypatch.setattr(ocr.s, "request", fake_request)
    ocr.mode = "grounding"
    pages = ocr.ocr_pdf(b"%PDF", [1])
    assert sent["refine_tables_with_ai"] == "false" and sent["save_to_disk"] == "false"
    assert pages[0].markdown == "x"
