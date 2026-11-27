"""JSON từ DocxConverter / DocxTranslator. Dữ liệu dưới đây hư cấu, chỉ mô phỏng cấu trúc của app/Sample Data."""
import json

import pytest

from pii_core import Segment, apply_replacements, mask_segments
from pii_core.documents import JsonPayloadDocument, JsonPayloadError, mask_document

PAYLOAD = {
    "table1": {"1": "NGÂN HÀNG TMCP ABC", "2": "Hà Nội, ngày 17 tháng 04 năm 2026"},
    "document": {
        "9": "Kính gửi: Ông ",                 # tên bị cắt qua 2 item (khác định dạng)
        "10": "Nguyễn Văn Bình",
        "11": " – Chức vụ Thành viên Ban điều hành",
        "12": "Thị trường chính: Mỹ, Hàn Quốc và Châu Âu.",
        "13": "Đầu mối: Đ/c Trần Minh Khoa, ĐT: 0934 468 777, Email: khoatm@abc.com.vn",
        "14": "Phòng họp tầng 25A –194 Trần Quang Khải",
    },
    "table2": {"20": "Nguyễn Văn Bình", "21": "PTGĐ Trần Minh Khoa (để b/c);", "22": "Chi nhánh Từ Liêm"},
}
CONVERT = {"output": {"sourceFile": "a.docx", "metadata": {"title": "", "author": "Dang Thi Mai"},
                      "sections": [{"type": "document", "file": "word/document.xml", "items": [
                          {"type": "text", "id": 1, "text": "Kính gửi: Ông "},
                          {"type": "text", "id": 2, "text": "Nguyễn Văn Bình", "formatting": {"bold": True}},
                          {"type": "text", "id": 3, "text": "."}]}]},
           "wtIndexMaps": {}, "contextMaps": {}, "prefixMaps": {}}


@pytest.fixture(scope="module")
def payload_out(analyze):
    return mask_document(json.dumps(PAYLOAD, ensure_ascii=False).encode(), "p.json", analyze)


def test_same_structure_and_keys(payload_out):
    out = json.loads(payload_out.data)
    assert {b: set(d) for b, d in out.items()} == {b: set(d) for b, d in PAYLOAD.items()}


def test_name_split_across_items(payload_out):
    out = json.loads(payload_out.data)["document"]
    assert out["9"] == "Kính gửi: Ông " and out["10"] == "<PERSON_1>"      # tag nằm ở item chứa tên


def test_propagation_masks_bare_and_titled_occurrences(payload_out):
    out = json.loads(payload_out.data)["table2"]
    assert out["20"] == "<PERSON_1>"                  # tên trơn trong ô bảng (không có "Ông")
    assert out["21"].startswith("PTGĐ <PERSON_")      # "PTGĐ + tên"
    assert payload_out.status == "done"


def test_no_false_positive_places_and_titles(payload_out):
    out = json.loads(payload_out.data)
    assert "Châu Âu" in out["document"]["12"]
    assert out["document"]["14"] == PAYLOAD["document"]["14"]          # tên đường sau số nhà "–194"
    assert out["table2"]["22"] == "Chi nhánh Từ Liêm"
    assert "Chức vụ" in out["document"]["11"]


def test_contact_line(payload_out):
    out = json.loads(payload_out.data)["document"]["13"]
    assert out == "Đầu mối: Đ/c <PERSON_2>, ĐT: <PHONE_1>, Email: <EMAIL_1>"


def test_convert_output_format_and_metadata(analyze):
    r = mask_document(json.dumps(CONVERT, ensure_ascii=False).encode(), "c.json", analyze)
    out = json.loads(r.data)
    items = out["output"]["sections"][0]["items"]
    assert [i["text"] for i in items] == ["Kính gửi: Ông ", "<PERSON_1>", "."]
    assert items[1]["formatting"] == {"bold": True}                   # giữ các trường khác
    assert out["output"]["metadata"]["author"] == ""
    assert r.leak.clean


def test_propagation_skips_low_confidence(analyze):
    # "Ham Jin Sik" (không phải họ Việt) chỉ nhận được nhờ "Ông" -> ô bảng chỉ có tên trơn phải nhờ lan truyền.
    # "Hoàng Mai" trơn (2 chữ, score thấp) không được lan sang chỗ khác.
    segs = [Segment("a", "Kính gửi Ông Ham Jin Sik."), Segment("b", "Ham Jin Sik"),
            Segment("c", "Quận Hoàng Mai"), Segment("d", "Hoàng Mai")]
    r = mask_segments(segs, analyze)
    outs = [apply_replacements(s.text, [x for x in r.replacements if x.seg == s.id]) for s in segs]
    assert outs[:2] == ["Kính gửi Ông <PERSON_1>.", "<PERSON_1>"]
    assert [x.recognizer for x in r.replacements if x.seg == "b"] == ["propagation"]
    assert outs[2] == "Quận Hoàng Mai"


def test_rejects_unknown_json():
    with pytest.raises(JsonPayloadError):
        JsonPayloadDocument(b"[1, 2]")
    with pytest.raises(JsonPayloadError):
        JsonPayloadDocument(b"{not json")
