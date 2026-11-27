"""Markdown OCR <-> text thuần. Dữ liệu hư cấu."""
from pii_core import Replacement
from pii_core.documents.markdown_text import MarkdownText

MD = """# Báo cáo
**Ông** **Nguyễn** Văn An &amp; bà Lê
<table><tr><td>Họ tên</td><td>Trần Thị Bích</td></tr><tr><td>ĐT</td><td>0912 345 678</td></tr></table>
- Mục 1: số 12 Đường Lê Lợi
---
| A | B |
|---|---|
| x | Phạm Văn C |"""


def rep(m, s, tag):
    i = m.text.index(s)
    return Replacement("s", i, i + len(s), "X", tag, 1.0)


def test_plain_text_strips_markup_and_splits_cells():
    t = MarkdownText(MD).text
    assert "Ông Nguyễn Văn An & bà Lê" in t
    assert "Họ tên\nTrần Thị Bích\nĐT\n0912 345 678" in t      # ô bảng không dính nhau
    assert "**" not in t and "<td>" not in t and "#" not in t and "---" not in t


def test_mask_keeps_markup_balanced():
    m = MarkdownText(MD)
    out = m.mask([rep(m, "Nguyễn Văn An", "<PERSON_1>"), rep(m, "Trần Thị Bích", "<PERSON_2>"),
                  rep(m, "0912 345 678", "<PHONE_1>"), rep(m, "Phạm Văn C", "<PERSON_3>")])
    assert "**Ông** **<PERSON_1>** &amp; bà Lê" in out
    assert "<td><PERSON_2></td>" in out and "<td><PHONE_1></td>" in out
    assert "| x | <PERSON_3> |" in out
    for v in ("Nguyễn", "Văn An", "Trần Thị Bích", "0912", "Phạm Văn C"):
        assert v not in out


def test_entity_inside_span_removed_whole():
    m = MarkdownText("Công ty A &amp; B - Ông Lê Văn Tám")
    out = m.mask([rep(m, "Lê Văn Tám", "<PERSON_1>")])
    assert out == "Công ty A &amp; B - Ông <PERSON_1>"
