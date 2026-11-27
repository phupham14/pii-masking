"""JSON trích từ DOCX bởi DocxConverter / DocxTranslator (dự án dịch tài liệu) -> che -> JSON cùng cấu trúc.

Hai dạng vào:
  - translation-payload : {bucket: {id: text}}               (bucket = document, table1, footer...)
  - convert-output      : {output: {sections: [{type, items: [{id, text, ...}]}], metadata}, wtIndexMaps, ...}

Mỗi item là MỘT KHÚC CÙNG ĐỊNH DẠNG, không phải paragraph: "Ông " | "Nguyễn Văn An" (in đậm) là 2 item.
Phân tích từng item riêng sẽ sót tên bị cắt, nên item liền nhau được ghép thành paragraph "ảo" khi:
id liên tiếp trong cùng bucket VÀ chỗ nối có dấu cách / dấu câu (tên bị cắt ở ranh giới từ luôn có dấu cách).
Không ghép được thì là segment riêng (ranh giới cứng cho recognizer). Tag đặt vào item đầu tiên mà span chạm tới,
phần còn lại của span bị cắt khỏi các item sau - giống cách xử lý run trong docx_handler.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from ..segments import Replacement, Segment

JOIN_AFTER = tuple(" \t (\"'“‘/-–")
JOIN_BEFORE = tuple(" \t ,.;:)\"'”’/-–")
META_CLEAR = ("author", "lastModifiedBy", "creator", "title", "subject", "company", "manager")


class JsonPayloadError(ValueError):
    pass


@dataclass
class _Item:
    path: tuple          # đường dẫn để ghi ngược vào JSON
    bucket: str
    id: str
    text: str


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


class JsonPayloadDocument:
    def __init__(self, data: bytes):
        try:
            self.obj = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise JsonPayloadError(f"JSON không hợp lệ: {e}") from e
        self.warnings: list[str] = []
        if isinstance(self.obj, dict) and isinstance(self.obj.get("output"), dict) and "sections" in self.obj["output"]:
            self.kind = "convert-output"
            self.items = [_Item(("output", "sections", si, "items", ii, "text"), f"{si}:{s.get('type', '')}",
                                str(it.get("id")), it.get("text") or "")
                          for si, s in enumerate(self.obj["output"]["sections"])
                          for ii, it in enumerate(s.get("items", [])) if isinstance(it.get("text"), str)]
            self._scrub_metadata()
        elif isinstance(self.obj, dict) and self.obj and all(isinstance(v, dict) for v in self.obj.values()):
            self.kind = "translation-payload"
            self.items = [_Item((b, k), b, k, v) for b, d in self.obj.items() for k, v in d.items() if isinstance(v, str)]
        else:
            raise JsonPayloadError("không nhận ra cấu trúc JSON (cần translation-payload hoặc convert-output)")
        if self.kind == "convert-output" and any(self.obj.get(k) for k in ("wtIndexMaps", "contextMaps", "prefixMaps")):
            self.warnings.append("wtIndexMaps/contextMaps/prefixMaps có thể chứa text gốc: chưa được che")
        self._groups = self._group()

    def _scrub_metadata(self):
        meta = self.obj["output"].get("metadata") or {}
        for k in META_CLEAR:
            if isinstance(meta.get(k), str) and meta[k].strip():
                meta[k] = ""

    @staticmethod
    def _joinable(prev: _Item, nxt: _Item) -> bool:
        a, b = _int(prev.id), _int(nxt.id)
        return (prev.bucket == nxt.bucket and a is not None and b == a + 1 and prev.text and nxt.text
                and (prev.text.endswith(JOIN_AFTER) or nxt.text.startswith(JOIN_BEFORE)))

    def _group(self):
        groups = []
        for it in self.items:
            if groups and self._joinable(groups[-1][-1], it):
                groups[-1].append(it)
            else:
                groups.append([it])
        return groups

    # ------------------------------------------------------------------ API giống DocxDocument
    def segments(self) -> list[Segment]:
        out = []
        for g in self._groups:
            pieces, pos = [], 0
            for it in g:
                pieces.append(dict(bucket=it.bucket, id=it.id, start=pos, end=pos + len(it.text)))
                pos += len(it.text)
            text = "".join(it.text for it in g)
            if text.strip():
                out.append(Segment(f"{g[0].bucket}#{g[0].id}", text, dict(kind="json", pieces=pieces)))
        return out

    def apply(self, replacements: list[Replacement]):
        groups = {f"{g[0].bucket}#{g[0].id}": g for g in self._groups}
        by_seg: dict[str, list[Replacement]] = {}
        for r in replacements:
            by_seg.setdefault(r.seg, []).append(r)
        for sid, reps in by_seg.items():
            g, reps = groups[sid], sorted(reps, key=lambda r: r.start)
            spans, pos = [], 0
            for it in g:
                spans.append((pos, pos + len(it.text), it))
                pos += len(it.text)
            anchor = {i: next(it for s, e, it in spans if s < r.end and r.start < e) for i, r in enumerate(reps)}
            for s, e, it in spans:
                hits = [(i, r) for i, r in enumerate(reps) if r.start < e and s < r.end]
                if not hits:
                    continue
                out, p = [], s
                for i, r in hits:
                    out.append(it.text[p - s:max(r.start, s) - s])
                    if anchor[i] is it:
                        out.append(r.tag)
                    p = min(r.end, e)
                out.append(it.text[p - s:])
                it.text = "".join(out)
                self._set(it.path, it.text)

    def _set(self, path, value):
        o = self.obj
        for k in path[:-1]:
            o = o[k]
        o[path[-1]] = value

    def save(self) -> bytes:
        return json.dumps(self.obj, ensure_ascii=False, indent=2).encode("utf-8")

    def raw_text_nodes(self):
        def walk(o, where):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from walk(v, f"{where}/{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    yield from walk(v, f"{where}/{i}")
            elif isinstance(o, str) and o.strip():
                yield where, o
        yield from walk(self.obj, "")

    def copy(self):
        return copy.deepcopy(self)
