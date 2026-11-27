"""Kiểm tra file output còn sót PII hay không, trước khi trả cho người dùng.

Ba lớp:
  1. value_hits  : giá trị gốc trong mapping còn xuất hiện trong output (quét MỌI node text / thuộc tính thô,
                   không qua extractor -> bắt cả chỗ extractor không đọc tới).
  2. value_hits  : như trên nhưng trên text đã ghép của từng segment (tên bị cắt qua nhiều run).
  3. redetections: chạy lại detector trên segment của output; span mà policy bảo che nhưng không phải tag.
Báo cáo KHÔNG chứa giá trị gốc (chỉ tag / nhãn / vị trí) -> lưu được vào DB.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import regex

from .engine import AnalyzeFn
from .policy import Policy, get_policy
from .pseudonymizer import Pseudonymizer
from .segments import Segment, analysis_text

MIN_VALUE_LEN = 3   # giá trị quá ngắn (tên 1-2 ký tự) dễ trùng chữ khác -> bỏ qua ở lớp 1-2


@dataclass
class LeakReport:
    value_hits: list[dict] = field(default_factory=list)      # {tag, label, where}
    redetections: list[dict] = field(default_factory=list)    # {seg, entity, start, end, recognizer}
    unprocessed: list[dict] = field(default_factory=list)     # phần không xử lý được (vd trang scan chưa OCR)
    warnings: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.value_hits and not self.redetections and not self.unprocessed

    def to_dict(self):
        return dict(clean=self.clean, **asdict(self))


def _value_pattern(value: str):
    v = " ".join(value.split())
    if len(v) < MIN_VALUE_LEN:
        return None
    body = r"\s+".join(regex.escape(t) for t in v.split(" "))
    return regex.compile(rf"(?<![\p{{L}}\p{{N}}]){body}(?![\p{{L}}\p{{N}}])")


def check(mapping: list[dict], raw_strings, segments: list[Segment], analyze: AnalyzeFn | None = None,
          policy: Policy | str | None = None, warnings=(), unprocessed=(), ignore=()) -> LeakReport:
    """raw_strings: iterable (where, chuỗi) - mọi text/thuộc tính thô của output.
    segments: segment trích lại từ output. unprocessed: phần handler không xử lý được -> không bao giờ "sạch".
    ignore: giá trị người review đã xác nhận KHÔNG phải PII (bỏ khỏi lớp chạy lại detector)."""
    ignored = {" ".join(v.split()) for v in ignore}
    policy = policy if isinstance(policy, Policy) else get_policy(policy)
    rep = LeakReport(warnings=list(warnings), unprocessed=list(unprocessed))
    pats = [(m, _value_pattern(m["original"])) for m in mapping]
    pats = [(m, p) for m, p in pats if p]

    seen = set()
    sources = list(raw_strings) + [(s.id, s.text) for s in segments]
    for m, p in pats:
        for where, s in sources:
            if (m["tag"], where) not in seen and p.search(s):
                seen.add((m["tag"], where))
                rep.value_hits.append(dict(tag=m["tag"], label=m["label"], where=where))

    if analyze is not None:
        for seg in segments:
            for r in analyze(analysis_text(seg.text)):
                if not policy.is_masked(r.entity_type):
                    continue
                span = seg.text[r.start:r.end]
                if Pseudonymizer.TAG_RE.search(span) or not any(c.isalnum() for c in span):
                    continue   # chính là tag đã che / span toàn khoảng trắng-dấu câu (pyvi lệch offset) -> không phải PII
                if " ".join(span.split()) in ignored:
                    continue   # người review đã bỏ che giá trị này
                rep.redetections.append(dict(seg=seg.id, entity=r.entity_type, start=r.start, end=r.end,
                                             recognizer=(r.recognition_metadata or {}).get("recognizer_name", "")))
    return rep
