"""Định dạng trung gian dùng chung cho mọi loại tài liệu: segments (text + locator) -> replacements.

Extractor (docx / pdf / ocr) sinh `Segment`, mỗi segment là một đơn vị text liền mạch (paragraph DOCX,
dòng/block PDF...). `meta` chứa thông tin riêng của định dạng để writer ghi ngược (part, locator, runs,
page, char_boxes...) và pii_core không đọc tới.

`mask_segments` trả về `Replacement` theo offset trong text GỐC của segment. Writer chỉ cần thay
[start, end) bằng `tag`. Cách giải span chồng nhau sao chép đúng AnonymizerEngine của Presidio, nên
`apply_replacements(text, reps)` che đúng những chỗ mà `AnonymizerEngine.anonymize` che. Khác duy nhất:
tag được đánh số theo thứ tự đọc (Presidio đánh từ cuối lên). Xem app/tests/test_pii_core.py.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

import regex
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import ConflictResolutionStrategy

from .engine import AnalyzeFn
from .policy import Policy, get_policy
from .postprocess import source
from .pseudonymizer import Pseudonymizer, tag_label

SCHEMA_VERSION = 1


@dataclass
class Segment:
    id: str
    text: str
    meta: dict = field(default_factory=dict)


@dataclass
class Replacement:
    seg: str
    start: int
    end: int
    entity: str
    tag: str
    score: float
    recognizer: str = ""


@dataclass
class MaskResult:
    replacements: list[Replacement]
    detections: list[Replacement]       # mọi span phát hiện được, kể cả loại "giữ" (tag rỗng)
    mapping: list[dict]                 # [{tag, label, original}] - DỮ LIỆU NHẠY CẢM

    def to_dict(self):
        return dict(schema_version=SCHEMA_VERSION,
                    replacements=[asdict(r) for r in self.replacements],
                    detections=[asdict(r) for r in self.detections])


_ENGINE = AnonymizerEngine()

# Khoảng trắng Unicode (NBSP U+00A0 hay gặp trong PDF / Word, thin space, figure space...) -> " " để luật dùng " "
# khớp được. Thay 1 ký tự -> 1 ký tự nên offset không đổi; text gốc trong file không bị đụng tới.
_SPACES = {ord(c): " " for c in "            "
                                "   　"}


def analysis_text(text: str) -> str:
    return text.translate(_SPACES)


def resolve_spans(text: str, results) -> list:
    """Kết quả analyzer -> danh sách (start, end, eff_end, result) theo thứ tự Presidio thao tác (từ cuối lên).

    Sao chép AnonymizerEngine.anonymize (presidio-anonymizer 2.2.x): sắp xếp, gộp span cùng loại giao nhau,
    bỏ span nằm gọn trong span khác, gộp span cùng loại cách nhau toàn dấu cách, rồi thay từ cuối lên:
    span đứng trước bị cắt `end` tới `start` của span đã thay sau nó (eff_end). Giá trị đưa vào operator
    luôn là text[start:end] chưa cắt - giống Presidio.
    """
    rs = [copy.copy(r) for r in results]   # Presidio _copy_recognizer_results làm mất recognition_metadata
    rs.sort(key=lambda x: (x.start, x.end))
    rs = _ENGINE._remove_conflicts_and_get_text_manipulation_data(
        rs, ConflictResolutionStrategy.MERGE_SIMILAR_OR_CONTAINED)
    rs = _ENGINE._merge_entities_with_spaces_between(text, rs)
    out, last = [], len(text)
    for r in sorted(rs, reverse=True):
        out.append((r.start, r.end, min(r.end, last), r))
        last = r.start
    return out


def mask_segment(seg: Segment, analyze: AnalyzeFn, policy: Policy, pseudo: Pseudonymizer):
    results = analyze(analysis_text(seg.text)) if seg.text.strip() else []
    detections = [Replacement(seg.id, r.start, r.end, r.entity_type, "", round(r.score, 3), source(r))
                  for r in sorted(results, key=lambda r: (r.start, r.end))]
    reps = []
    # resolve_spans trả từ cuối lên (cách Presidio cắt span); đánh số tag theo thứ tự đọc từ đầu xuống
    for start, end, eff_end, r in reversed(resolve_spans(analysis_text(seg.text), results)):
        op = policy.operator_for(r.entity_type)
        if op.operator_name == "keep":
            continue
        if op.operator_name != "replace":
            raise ValueError(f"policy {policy.name}: operator '{op.operator_name}' cho {r.entity_type} "
                             "không hỗ trợ (chỉ replace / keep)")
        t = pseudo.tag(tag_label(op), seg.text[start:end])
        reps.append(Replacement(seg.id, start, eff_end, r.entity_type, t, round(r.score, 3), source(r)))
    return reps, detections


PROPAGATE_MIN_SCORE = 0.85     # tên có danh xưng / ngữ cảnh mạnh
PROPAGATE_MIN_WORDS = 3        # hoặc tên đủ dài để khó trùng chữ thường
PROPAGATE_MIN_CHARS = 6        # mã số / SĐT / email


def _propagatable(rep: Replacement, value: str) -> bool:
    if rep.entity == "PERSON":
        return rep.score >= PROPAGATE_MIN_SCORE or len(value.split()) >= PROPAGATE_MIN_WORDS
    return len(value.strip()) >= PROPAGATE_MIN_CHARS


def propagate(segments: list[Segment], reps: list[Replacement]) -> list[Replacement]:
    """Giá trị đã phát hiện CHẮC CHẮN ở một chỗ -> che mọi chỗ khác nó xuất hiện nguyên văn (cùng tag).

    Recognizer dựa vào ngữ cảnh ("Ông X") nên hay sót cùng tên đứng trơn ở ô bảng / chữ ký / "PTGĐ X".
    Chỉ lan giá trị đủ chắc (xem _propagatable) để không nhân lỗi che nhầm (vd "Từ Liêm" bị nhận là tên).
    """
    texts = {s.id: s.text for s in segments}
    values = {}   # chuỗi chuẩn hoá -> replacement mẫu
    for r in reps:
        v = " ".join(texts[r.seg][r.start:r.end].split())
        if _propagatable(r, v):
            values.setdefault(v, r)
    if not values:
        return reps
    alts = sorted(values, key=len, reverse=True)
    pat = regex.compile(r"(?<![\p{L}\p{N}@.])(?:" + "|".join(r"\s+".join(regex.escape(t) for t in v.split(" "))
                                                            for v in alts) + r")(?![\p{L}\p{N}@])")
    taken: dict[str, list[tuple[int, int]]] = {}
    for r in reps:
        taken.setdefault(r.seg, []).append((r.start, r.end))
    extra = []
    for seg in segments:
        for m in pat.finditer(seg.text):
            if any(m.start() < e and s < m.end() for s, e in taken.get(seg.id, [])):
                continue
            src = values[" ".join(m.group(0).split())]
            extra.append(Replacement(seg.id, m.start(), m.end(), src.entity, src.tag, src.score, "propagation"))
    order = {s.id: i for i, s in enumerate(segments)}
    return sorted(reps + extra, key=lambda r: (order[r.seg], r.start))


def mask_segments(segments: list[Segment], analyze: AnalyzeFn, policy: Policy | str | None = None,
                  pseudo: Pseudonymizer | None = None, propagate_values: bool = True) -> MaskResult:
    """Một tài liệu = một Pseudonymizer: cùng giá trị ở các segment khác nhau nhận cùng tag."""
    policy = policy if isinstance(policy, Policy) else get_policy(policy)
    pseudo = pseudo or Pseudonymizer()
    reps, dets = [], []
    for seg in segments:
        r, d = mask_segment(seg, analyze, policy, pseudo)
        reps += r
        dets += d
    if propagate_values:
        reps = propagate(segments, reps)
    return MaskResult(reps, dets, pseudo.mapping_json())


def apply_replacements(text: str, reps: list[Replacement]) -> str:
    """Thay các span (không chồng nhau) của MỘT segment, từ cuối lên."""
    for r in sorted(reps, key=lambda r: r.start, reverse=True):
        text = text[:r.start] + r.tag + text[r.end:]
    return text
