"""Review: người dùng sửa danh sách chỗ che (bỏ che nhầm / thêm chỗ bị sót) trước khi xuất lại file.

    edits = {"remove": [{"seg", "start", "end", "all": bool}],
             "add":    [{"seg", "start", "end", "entity", "all": bool}]}
    res = apply_edits(segments, replacements, mapping, policy, edits, ignored)

- remove: bỏ replacement có đúng (seg, start, end); all=True -> bỏ mọi chỗ có CÙNG giá trị.
  Giá trị bị bỏ vào `ignored` -> leak-check không báo lại (detector sẽ tìm ra nó lần nữa).
- add: thêm span bất kỳ trong segment, entity phải là loại policy CHE; tag theo mapping cũ (cùng giá trị = cùng tag,
  giá trị mới = số tiếp theo). all=True -> thêm mọi chỗ cùng giá trị chưa bị che.
- mapping tính lại chỉ gồm tag còn được dùng.
"""
from __future__ import annotations

from dataclasses import dataclass

import regex

from .policy import Policy, get_policy
from .pseudonymizer import Pseudonymizer, tag_label
from .segments import Replacement, Segment


class ReviewError(ValueError):
    pass


@dataclass
class ReviewResult:
    replacements: list[Replacement]
    mapping: list[dict]
    ignored: list[str]
    removed: int
    added: int


def _norm(s: str) -> str:
    return " ".join(s.split())


def _occurrences(segments: list[Segment], value: str):
    pat = regex.compile(r"(?<![\p{L}\p{N}])" + r"\s+".join(regex.escape(t) for t in value.split(" "))
                        + r"(?![\p{L}\p{N}])")
    for s in segments:
        for m in pat.finditer(s.text):
            yield s.id, m.start(), m.end()


def apply_edits(segments: list[Segment], replacements: list[Replacement], mapping: list[dict],
                policy: Policy | str | None, edits: dict, ignored=()) -> ReviewResult:
    policy = policy if isinstance(policy, Policy) else get_policy(policy)
    texts = {s.id: s.text for s in segments}
    reps = list(replacements)
    ignored = {_norm(v) for v in ignored}
    value = lambda r: _norm(texts[r.seg][r.start:r.end])  # noqa: E731
    removed = added = 0

    for e in edits.get("remove") or []:
        hit = next((r for r in reps if (r.seg, r.start, r.end) == (e.get("seg"), e.get("start"), e.get("end"))), None)
        if hit is None:
            raise ReviewError(f"không có chỗ che {e.get('seg')}[{e.get('start')}:{e.get('end')}]")
        v = value(hit)
        drop = [r for r in reps if value(r) == v] if e.get("all") else [hit]
        reps = [r for r in reps if r not in drop]
        removed += len(drop)
        ignored.add(v)

    pseudo = Pseudonymizer.from_mapping(mapping)
    for e in edits.get("add") or []:
        seg, start, end, entity = e.get("seg"), e.get("start"), e.get("end"), e.get("entity")
        if seg not in texts:
            raise ReviewError(f"không có segment {seg}")
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(texts[seg])):
            raise ReviewError(f"vị trí không hợp lệ {seg}[{start}:{end}]")
        if not entity or not policy.is_masked(entity) or entity not in policy.operators:
            raise ReviewError(f"loại '{entity}' không phải loại bị che trong policy '{policy.name}'")
        v = _norm(texts[seg][start:end])
        if not any(c.isalnum() for c in v):
            raise ReviewError("đoạn chọn không có chữ / số")
        tag = pseudo.tag(tag_label(policy.operator_for(entity)), texts[seg][start:end])
        spots = list(_occurrences(segments, v)) if e.get("all") else [(seg, start, end)]
        if (seg, start, end) not in spots:
            spots.insert(0, (seg, start, end))
        for sid, s, t in spots:
            if any(r.seg == sid and r.start < t and s < r.end for r in reps):
                if (sid, s, t) == (seg, start, end):
                    raise ReviewError(f"{seg}[{start}:{end}] chồng lên chỗ đang che")
                continue                                  # chỗ khác đã che rồi -> bỏ qua
            reps.append(Replacement(sid, s, t, entity, tag, 1.0, "manual"))
            added += 1
        ignored.discard(v)

    order = {s.id: i for i, s in enumerate(segments)}
    reps.sort(key=lambda r: (order.get(r.seg, 1 << 30), r.start))
    used = {r.tag for r in reps}
    new_mapping = [m for m in pseudo.mapping_json() if m["tag"] in used]
    return ReviewResult(reps, new_mapping, sorted(ignored), removed, added)
