"""Test DOCX handler. File mẫu được dựng trong test, mọi tên / số / địa chỉ đều hư cấu."""
import io
import re
import zipfile

import docx
import pytest
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.shared import Pt

from pii_core import mask_segments
from pii_core.documents import DocxError, UnsupportedFormat, extract, mask_document, render

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:v="urn:schemas-microsoft-com:vml"')

# Giá trị phải biến mất khỏi file output (kể cả trong XML thô)
MUST_GO = ["Nguyễn Văn An", "079185012345", "Lê Thị Hoa", "0912 345 678", "binh.tran@example.com",
           "Phạm Thị Lan", "Lê Lợi", "lan.pham@example.com", "Trần Văn Bình", "Hoàng Minh Tuấn",
           "Đỗ Văn Hùng", "Nguyen Van A", "0071000123456"]
# Giá trị phải còn nguyên
MUST_STAY = ["Đường Pasteur", "Phường Bến Nghé", "500.000.000", "Ngân hàng TMCP Ngoại thương Việt Nam"]


def build_docx() -> bytes:
    d = docx.Document()
    d.core_properties.author = "Phạm Thị Lan"
    d.core_properties.last_modified_by = "Nguyen Van A"
    d.core_properties.title = "Hồ sơ Ông Trần Văn Bình"

    # 1. tên + CCCD bị cắt qua nhiều run, có run in đậm
    p = d.add_paragraph()
    p.add_run("Ông ")
    b = p.add_run("Nguyễn")
    b.bold = True
    p.add_run(" Văn An, CCCD số 0791")
    p.add_run("85012345, khoản vay 500.000.000 VND.")
    for r in p.runs:
        r.font.size = Pt(11)

    # 2. địa chỉ cá nhân có xuống dòng (w:br) ở giữa
    p = d.add_paragraph("Bà Phạm Thị Lan thường trú tại số 12 Đường Lê Lợi,")
    p.runs[0].add_break()
    p.add_run("Phường Bến Thành, Quận 1, Thành phố Hồ Chí Minh.")

    # 3. địa chỉ tổ chức (giữ)
    d.add_paragraph("Công ty có trụ sở chính tại số 111A, Đường Pasteur, Phường Bến Nghé, Quận 1. "
                    "Số tài khoản 0071000123456 của bà Lan tại Ngân hàng TMCP Ngoại thương Việt Nam.")

    # 4. bảng + bảng lồng
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Kiểm toán viên"
    t.cell(0, 1).text = "Lê Thị Hoa"
    inner = t.cell(1, 1).add_table(rows=1, cols=1)
    inner.cell(0, 0).text = "Điện thoại 0912 345 678"

    # 5. header / footer
    sec = d.sections[0]
    sec.header.paragraphs[0].text = "Liên hệ: binh.tran@example.com"
    sec.footer.paragraphs[0].text = "Người lập: Bà Phạm Thị Lan"

    # 6. hyperlink mailto (đích nằm trong document.xml.rels) + field code HYPERLINK
    rid = d.part.relate_to("mailto:binh.tran@example.com", RT.HYPERLINK, is_external=True)
    p = d.add_paragraph("Email: ")
    p._p.append(parse_xml(f'<w:hyperlink {NS} r:id="{rid}"><w:r><w:t>binh.tran@example.com</w:t></w:r></w:hyperlink>'))
    p = d.add_paragraph("Gửi ")
    for xml in ('<w:r {ns}><w:fldChar w:fldCharType="begin"/></w:r>',
                '<w:r {ns}><w:instrText xml:space="preserve"> HYPERLINK "mailto:lan.pham@example.com" </w:instrText></w:r>',
                '<w:r {ns}><w:fldChar w:fldCharType="separate"/></w:r>',
                '<w:r {ns}><w:t>chị Lan</w:t></w:r>',
                '<w:r {ns}><w:fldChar w:fldCharType="end"/></w:r>'):
        p._p.append(parse_xml(xml.format(ns=NS)))

    # 7. track changes: đoạn bị xoá (phải biến mất) + đoạn được chèn (giữ, che)
    p = d.add_paragraph("Người đại diện: ")
    p._p.append(parse_xml(f'<w:del {NS} w:id="91" w:author="Nguyen Van A" w:date="2026-01-01T00:00:00Z">'
                          f'<w:r><w:delText>Ông Trần Văn Bình</w:delText></w:r></w:del>'))
    p._p.append(parse_xml(f'<w:ins {NS} w:id="92" w:author="Nguyen Van A" w:date="2026-01-01T00:00:00Z">'
                          f'<w:r><w:t>Ông Hoàng Minh Tuấn</w:t></w:r></w:ins>'))

    # 8. textbox (paragraph lồng trong run của paragraph khác)
    p = d.add_paragraph("Ghi chú: ")
    p._p.append(parse_xml(f'<w:r {NS}><w:pict><v:shape style="width:200pt;height:40pt"><v:textbox>'
                          f'<w:txbxContent><w:p><w:r><w:t>Ông Đỗ Văn Hùng</w:t></w:r></w:p></w:txbxContent>'
                          f'</v:textbox></v:shape></w:pict></w:r>'))

    # 9. comment: tác giả + nội dung có PII
    p = d.add_paragraph("Đã đối chiếu hồ sơ.")
    d.add_comment(p.runs, text="Kiểm tra lại CCCD 079185012345", author="Nguyen Van A", initials="NVA")

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def raw_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                         if n.endswith(".xml") or n.endswith(".rels"))


def all_text(data: bytes) -> str:
    """XML thô + XML đã bỏ hết thẻ (giá trị bị cắt qua nhiều run nối lại thành một)."""
    raw = raw_xml(data)
    return raw + "\n" + re.sub(r"<[^>]+>", "", raw)


@pytest.fixture(scope="module")
def sample():
    return build_docx()


@pytest.fixture(scope="module")
def masked(sample, analyze):
    return mask_document(sample, "hoso.docx", analyze, "bctc")


def test_source_really_contains_pii(sample):
    text = all_text(sample)
    for v in MUST_GO:
        assert v in text, v   # file mẫu dựng đúng


def test_pii_removed_from_raw_xml(masked):
    text = all_text(masked.data)
    for v in MUST_GO:
        assert v not in text, v


def test_kept_values_stay(masked):
    raw = raw_xml(masked.data)
    for v in MUST_STAY:
        assert v in raw, v


def test_leak_check_clean(masked):
    assert masked.leak.value_hits == [] and masked.leak.redetections == []
    assert masked.status == "done"


def test_output_is_valid_docx_and_formatting_kept(masked):
    d = docx.Document(io.BytesIO(masked.data))
    p = d.paragraphs[0]
    assert p.text.startswith("Ông <PERSON_1>, CCCD số <ID_CARD_1>")
    bold = [r for r in p.runs if r.bold]
    assert bold and bold[0].text == "<PERSON_1>"          # tag nằm ở run in đậm (run đầu tiên của span)
    assert d.sections[0].header.paragraphs[0].text == "Liên hệ: <EMAIL_1>"
    assert d.sections[0].footer.paragraphs[0].text == "Người lập: Bà <PERSON_2>"
    tag = d.tables[0].cell(0, 1).text
    assert {m["tag"]: m["original"] for m in masked.result.mapping}[tag] == "Lê Thị Hoa"


def test_same_person_same_tag_everywhere(masked):
    d = docx.Document(io.BytesIO(masked.data))
    assert "Bà <PERSON_2> thường trú" in d.paragraphs[1].text        # thân văn bản = footer
    orig = {m["tag"]: m["original"] for m in masked.result.mapping}
    assert orig["<PERSON_2>"] == "Phạm Thị Lan"


def test_address_split_by_linebreak_masked_both_lines(masked):
    # luật địa chỉ coi xuống dòng là ranh giới -> 2 tag, cả 2 dòng đều được che, xuống dòng giữ nguyên
    d = docx.Document(io.BytesIO(masked.data))
    assert d.paragraphs[1].text == "Bà <PERSON_2> thường trú tại <ADDRESS_1>,\n<ADDRESS_2>."


def test_linebreak_and_tab_inside_span_removed():
    from pii_core import Replacement
    from pii_core.documents import DocxDocument
    d = docx.Document()
    p = d.add_paragraph("A ")
    r = p.add_run("x1")
    r.add_break()
    r2 = p.add_run("y2")
    r2.add_tab()
    p.add_run("z3 B")
    buf = io.BytesIO()
    d.save(buf)
    doc = DocxDocument(buf.getvalue())
    seg = next(s for s in doc.segments() if s.text.startswith("A "))
    assert seg.text == "A x1\ny2\tz3 B"
    doc.apply([Replacement(seg.id, 2, 10, "PERSON", "<PERSON_1>", 1.0)])
    out = docx.Document(io.BytesIO(doc.save()))
    assert out.paragraphs[0].text == "A <PERSON_1> B"
    assert "<w:br/>" not in raw_xml(doc.save()) and "<w:tab/>" not in raw_xml(doc.save())


def test_track_changes_accepted_and_authors_scrubbed(masked):
    raw = raw_xml(masked.data)
    assert "<w:del " not in raw and "<w:ins " not in raw and "delText" not in raw
    assert 'w:author="Author"' in raw and "NVA" not in raw


def test_metadata_scrubbed(masked):
    cp = docx.Document(io.BytesIO(masked.data)).core_properties
    assert cp.author == "" and cp.last_modified_by == ""
    assert cp.title == "Hồ sơ Ông <PERSON_4>" or "Trần Văn Bình" not in cp.title


def test_extract_mask_render_equals_one_shot(sample, analyze, masked):
    """Bước tách analyze -> render (dùng cho review sau này) cho ra đúng file như chạy một mạch."""
    segs = extract(sample, "hoso.docx")
    assert [s.id for s in segs] == [s.id for s in extract(sample, "hoso.docx")]   # id ổn định
    res = mask_segments(segs, analyze, "bctc")
    assert render(sample, "hoso.docx", res.replacements) == masked.data


def test_rejects_bad_input():
    with pytest.raises(UnsupportedFormat):
        extract(b"x", "a.doc")
    with pytest.raises(DocxError):
        extract(b"not a zip", "a.docx")
