"""pii_core.review: sửa danh sách chỗ che. Dữ liệu hư cấu."""
import pytest

from pii_core import Pseudonymizer, Segment, apply_replacements, mask_segments
from pii_core.review import ReviewError, apply_edits

# "ZX-4471": mã nội bộ không luật nào bắt -> người review tự thêm
SEGS = [Segment("a", "Ông Nguyễn Văn An gặp đối tác, mã hồ sơ ZX-4471."),
        Segment("b", "Ký tên: Nguyễn Văn An. Hồ sơ ZX-4471 lưu kho."),
        Segment("c", "Liên hệ Trần Minh Khoa.")]


def masked(analyze):
    return mask_segments(SEGS, analyze)


def out(reps):
    return [apply_replacements(s.text, [r for r in reps if r.seg == s.id]) for s in SEGS]


def test_from_mapping_continues_numbering():
    p = Pseudonymizer.from_mapping([{"tag": "<PERSON_3>", "original": "Lê  Văn A"}])
    assert p.tag("PERSON", "Lê Văn A") == "<PERSON_3>"       # giá trị cũ (chuẩn hoá khoảng trắng) giữ tag
    assert p.tag("PERSON", "Lê Văn B") == "<PERSON_4>"       # giá trị mới nhận số tiếp


def test_remove_all_and_add_all(analyze):
    r = masked(analyze)
    an = next(x for x in r.replacements if SEGS[0].text[x.start:x.end] == "Nguyễn Văn An")
    i = SEGS[0].text.index("ZX-4471")
    res = apply_edits(SEGS, r.replacements, r.mapping, "bctc",
                      {"remove": [{"seg": "a", "start": an.start, "end": an.end, "all": True}],
                       "add": [{"seg": "a", "start": i, "end": i + 7, "entity": "VN_ID_CARD", "all": True}]})
    o = out(res.replacements)
    assert "Nguyễn Văn An" in o[0] and "Nguyễn Văn An" in o[1]          # bỏ ở mọi chỗ
    assert o[0].endswith("mã hồ sơ <ID_CARD_1>.") and "Hồ sơ <ID_CARD_1> lưu kho" in o[1]   # thêm mọi chỗ, cùng tag
    assert res.removed == 2 and res.added == 2
    assert res.ignored == ["Nguyễn Văn An"]
    assert "Nguyễn Văn An" not in {m["original"] for m in res.mapping}  # tag không còn dùng -> khỏi mapping
    assert {m["original"] for m in res.mapping} >= {"ZX-4471", "Trần Minh Khoa"}


def test_readding_ignored_value_unignores(analyze):
    r = masked(analyze)
    k = next(x for x in r.replacements if x.seg == "c")
    res = apply_edits(SEGS, r.replacements, r.mapping, "bctc",
                      {"remove": [{"seg": "c", "start": k.start, "end": k.end}]})
    res2 = apply_edits(SEGS, res.replacements, res.mapping, "bctc",
                       {"add": [{"seg": "c", "start": k.start, "end": k.end, "entity": "PERSON"}]}, res.ignored)
    assert res2.ignored == [] and out(res2.replacements)[2] == f"Liên hệ {k.tag}."   # tag cũ được dùng lại


@pytest.mark.parametrize("edit", [
    {"add": [{"seg": "zzz", "start": 0, "end": 1, "entity": "PERSON"}]},
    {"add": [{"seg": "a", "start": 5, "end": 2, "entity": "PERSON"}]},
    {"add": [{"seg": "a", "start": 0, "end": 3, "entity": "LOCATION"}]},
    {"add": [{"seg": "a", "start": 3, "end": 4, "entity": "PERSON"}]},       # chỉ khoảng trắng
    {"remove": [{"seg": "a", "start": 0, "end": 2}]},
])
def test_invalid(analyze, edit):
    r = masked(analyze)
    with pytest.raises(ReviewError):
        apply_edits(SEGS, r.replacements, r.mapping, "bctc", edit)
