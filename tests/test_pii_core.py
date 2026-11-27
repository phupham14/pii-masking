"""Test pii_core. Mọi tên, số định danh, địa chỉ dưới đây là hư cấu."""
import pytest
from presidio_anonymizer import AnonymizerEngine

from pii_core import (
    Pseudonymizer,
    Segment,
    apply_replacements,
    get_policy,
    mask_segments,
)
from pii_core.segments import mask_segment

SAMPLE = (
    "Bên liên quan: Ông Trần Văn Bình, CCCD số 079085001234, hộ chiếu C1234567, thường trú tại "
    "số 12 Đường Lê Lợi, Phường Bến Thành, Quận 1, Thành phố Hồ Chí Minh; mã số thuế cá nhân 8012345678; "
    "số tài khoản 0071000123456 tại Ngân hàng TMCP Ngoại thương Việt Nam; điện thoại 0912345678; "
    "email binh.tran@example.com. Khoản cho vay cá nhân: Bà Phạm Thị Lan, số dư 500.000.000 VND. "
    "Kiểm toán viên Lê Thị Hoa, Giấy chứng nhận đăng ký hành nghề kiểm toán số 1234-2023-001-1. "
    "Công ty có trụ sở chính tại số 111A, Đường Pasteur, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh, "
    "Giấy chứng nhận đăng ký doanh nghiệp số 0301429113; tài khoản số 1234567890 của Công ty tại Ngân hàng."
)
# (chuỗi, phải bị che với policy bctc?)
CHECKS = [
    ("Trần Văn Bình", True), ("Phạm Thị Lan", True), ("Lê Thị Hoa", True), ("079085001234", True),
    ("C1234567", True), ("Đường Lê Lợi", True), ("8012345678", True), ("0071000123456", True),
    ("0912345678", True), ("binh.tran@example.com", True), ("1234-2023-001-1", True),
    ("500.000.000", False), ("Đường Pasteur", False), ("0301429113", True), ("1234567890", True),
    ("Thành phố Hồ Chí Minh", False),
]


def masked_text(analyze, text, policy="bctc"):
    res = mask_segments([Segment("s", text)], analyze, policy)
    return apply_replacements(text, res.replacements), res


def test_bctc_policy_selftest(analyze):
    out, _ = masked_text(analyze, SAMPLE)
    for s, must_mask in CHECKS:
        assert (s not in out) == must_mask, s


def test_personal_policy_keeps_org_ids(analyze):
    out, _ = masked_text(analyze, SAMPLE, "personal")
    assert "0301429113" in out and "1234567890" in out          # MST, TK của công ty: giữ
    assert "8012345678" not in out and "0071000123456" not in out  # của cá nhân: che


def test_same_value_same_tag_across_segments(analyze):
    segs = [Segment("a", "Ông Nguyễn Văn An ký hợp đồng."), Segment("b", "Bà Lê Thị Hoa và ông Nguyễn Văn An.")]
    res = mask_segments(segs, analyze)
    tags = {(r.seg, segs[0 if r.seg == "a" else 1].text[r.start:r.end]): r.tag for r in res.replacements}
    assert tags[("a", "Nguyễn Văn An")] == tags[("b", "Nguyễn Văn An")] == "<PERSON_1>"
    assert {m["original"] for m in res.mapping} == {"Nguyễn Văn An", "Lê Thị Hoa"}


def test_replacements_are_ordered_and_disjoint(analyze, eval_texts):
    for text in eval_texts:
        reps, _ = mask_segment(Segment("s", text), analyze, get_policy(), Pseudonymizer())
        for a, b in zip(reps, reps[1:]):
            assert a.start < a.end <= b.start


def test_equivalent_to_presidio_anonymizer(analyze, eval_texts):
    """mask_segment che đúng những chỗ AnonymizerEngine (đường cũ của CC1) che, cùng tập giá trị trong mapping.
    Chỉ khác số thứ tự tag (theo thứ tự đọc thay vì từ cuối lên) -> so sau khi bỏ số."""
    engine, policy = AnonymizerEngine(), get_policy()
    unnumber = lambda s: Pseudonymizer.TAG_RE.sub(lambda m: "<" + m.group(1).rsplit("_", 1)[0] + ">", s)  # noqa: E731
    p_old, p_new = Pseudonymizer(), Pseudonymizer()
    for i, text in enumerate(eval_texts + [SAMPLE]):
        results = analyze(text)
        old = engine.anonymize(text=text, analyzer_results=results, operators=p_old.operators(policy)).text
        reps, _ = mask_segment(Segment(str(i), text), lambda _t, r=results: r, policy, p_new)
        assert unnumber(apply_replacements(text, reps)) == unnumber(old), text
    assert sorted(p_old.forward) == sorted(p_new.forward)


@pytest.mark.parametrize("text, expected", [
    # SĐT: mã vùng + ". " (BCTC FPT 2023)
    ("Điện thoại liên hệ/Tel.: 024. 7300 7300", "Điện thoại liên hệ/Tel.: <PHONE_1>"),
    ("Fax: 024. 3768 7410", "Fax: <PHONE_1>"),
    ("Nhà số 12. 0283 822 1234 là số bàn", "Nhà số 12. <PHONE_1> là số bàn"),   # không nuốt "12."
    ("Mã 0243. Tiếp theo", "Mã 0243. Tiếp theo"),
    ("Số tiền 1.234. 567 đồng", "Số tiền 1.234. 567 đồng"),
    # "các/những + danh xưng" in hoa = cách gọi chung; tên không kéo sang "của / công ty"
    ("GỬI CÁC CÔ ĐÔNG CỦA CÔNG TY CỔ PHẦN FPT", "GỬI CÁC CÔ ĐÔNG CỦA CÔNG TY CỔ PHẦN FPT"),
    ("ÔNG NGUYỄN VĂN AN - TỔNG GIÁM ĐỐC", "ÔNG <PERSON_1> - TỔNG GIÁM ĐỐC"),
    ("Ông Nguyễn Văn An của Công ty X", "Ông <PERSON_1> của Công ty X"),
    ("các ông Trần Văn Bình và Lê Văn Cường", "các ông <PERSON_1> và <PERSON_2>"),
])
def test_rule_fixes_2026_11_27(analyze, text, expected):
    assert masked_text(analyze, text)[0] == expected


def test_tags_numbered_in_reading_order(analyze):
    out, _ = masked_text(analyze, "Ông Nguyễn Văn An và bà Lê Thị Hoa, sau đó ông Nguyễn Văn An.")
    assert out == "Ông <PERSON_1> và bà <PERSON_2>, sau đó ông <PERSON_1>."


def test_deterministic_recognizer_order():
    from pii_core.engine import OrderedRegistry
    from pii_core.recognizers import build_vn_extra_recognizers
    reg = OrderedRegistry(supported_languages=["en"])
    recs = build_vn_extra_recognizers()
    for r in recs:
        reg.add_recognizer(r)
    got = reg.get_recognizers(language="en", entities=["PERSON", "VN_ADDRESS", "VN_TAX_CODE"])
    assert [r.name for r in got] == [r.name for r in recs if set(r.supported_entities) & {"PERSON", "VN_ADDRESS",
                                                                                          "VN_TAX_CODE"}]
