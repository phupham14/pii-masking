"""Markdown (kết quả OCR) <-> text thuần có ánh xạ vị trí.

Luật nhận diện chạy trên text thuần: bỏ markup (**đậm**, #, -, |, `, thẻ HTML, &entity;), ô / dòng bảng HTML và
<br> thành xuống dòng để nội dung các ô không dính nhau. Mỗi ký tự text thuần nhớ vị trí trong markdown
(ký tự chèn thêm như xuống dòng giữa các ô thì không có vị trí).

Khi che: tag đặt vào vị trí ký tự đầu của span, các ký tự CHỮ còn lại của span bị xoá, markup nằm giữa giữ nguyên
-> "**Nguyễn** Văn An" thành "**<PERSON_1>** " chứ không làm vỡ cặp **.
"""
from __future__ import annotations

import html
import re

from ..segments import Replacement

_BLOCK_BREAK = re.compile(r"(?i)</?(td|th|tr|table|thead|tbody|p|div|li|ul|ol|h\d)\b[^>]*>|<br\s*/?>")
_TAG = re.compile(r"<[^>]{1,200}>")
_ENTITY = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")
_LINE_PREFIX = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d+[.)][ \t]+)*")
_EMPH = re.compile(r"\*\*|__|(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])|`")
_HRULE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$")


class MarkdownText:
    def __init__(self, md: str):
        self.md = md
        chars: list[str] = []
        idx: list[int] = []

        def emit(c, i):
            chars.append(c)
            idx.append(i)

        pos = 0
        for line in md.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            if _HRULE.match(body) or body.strip().startswith("|---") or re.fullmatch(r"[\s|:-]+", body or "x"):
                pos += len(line)
                if chars and chars[-1] != "\n":
                    emit("\n", -1)
                continue
            start = _LINE_PREFIX.match(body).end()
            # vùng bỏ qua: thẻ HTML, markup nhấn mạnh, dấu | của bảng markdown
            skip = [False] * len(body)
            breaks = set()
            for m in _BLOCK_BREAK.finditer(body):
                for k in range(m.start(), m.end()):
                    skip[k] = True
                breaks.add(m.start())
            for m in _TAG.finditer(body):
                for k in range(m.start(), m.end()):
                    skip[k] = True
            for m in _EMPH.finditer(body):
                for k in range(m.start(), m.end()):
                    skip[k] = True
            for k in range(start):
                skip[k] = True
            k = start
            while k < len(body):
                if k in breaks and chars and chars[-1] != "\n":
                    emit("\n", -1)
                if skip[k]:
                    k += 1
                    continue
                m = _ENTITY.match(body, k)
                if m:                                 # &amp; -> "&" (ánh xạ về ký tự đầu của entity)
                    for c in html.unescape(m.group(0)):
                        emit(c, pos + k)
                    k = m.end()
                    continue
                c = body[k]
                emit(" " if c == "|" else c, pos + k)
                k += 1
            if chars and chars[-1] != "\n":
                emit("\n", -1)
            pos += len(line)
        self.text = "".join(chars)
        self.idx = idx

    def mask(self, reps: list[Replacement]) -> str:
        """Áp replacement (offset theo self.text) vào markdown."""
        out = list(self.md)
        insert: dict[int, str] = {}
        delete: set[int] = set()
        for r in reps:
            positions = [self.idx[i] for i in range(r.start, r.end) if self.idx[i] >= 0]
            if not positions:
                continue
            first = positions[0]
            insert[first] = r.tag
            for p in positions:
                # một ký tự markdown có thể sinh nhiều ký tự text (&amp;) -> chỉ xoá theo vị trí markdown
                delete.add(p)
                if self.md.startswith("&", p):
                    m = _ENTITY.match(self.md, p)
                    if m:
                        delete.update(range(p, m.end()))
        res = []
        for i, c in enumerate(out):
            if i in insert:
                res.append(insert[i])
            if i not in delete:
                res.append(c)
        return "".join(res)


def markdown_to_plain(md: str) -> str:
    return MarkdownText(md).text
