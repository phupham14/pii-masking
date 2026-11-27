"""DOCX: unzip -> XML -> Segment -> (pii_core) -> vá text tại chỗ trong XML gốc -> zip lại.

Nguyên tắc: XML gốc là nguồn chuẩn. Chỉ sửa node text (w:t / a:t), xoá node tab/xuống dòng nằm trong vùng che,
và vài thuộc tính chứa PII. Các part không đụng tới được chép nguyên byte.

Những gì được quét (mỗi mục = 1 Segment):
  - Paragraph WordprocessingML (w:p) ở mọi part word/*.xml: thân, bảng (cả bảng lồng), header/footer,
    footnote/endnote, comment, textbox (w:txbxContent, tính riêng, không gộp vào paragraph chứa nó), glossary.
  - Paragraph DrawingML (a:p) trong word/**: SmartArt, chữ trong chart, shape.
  - Field code (w:instrText, w:fldSimple/@w:instr), vd HYPERLINK "mailto:...".
  - Alt-text ảnh (wp:docPr/@descr, @title), đích hyperlink ngoài trong *.rels (mailto:...).
  - Metadata docProps/core.xml (title, subject, description, keywords, category).
Dọn thêm: accept track changes (bỏ w:del, gỡ w:ins), xoá tên tác giả (w:author, w:initials...),
xoá creator / lastModifiedBy / Company / Manager, giá trị custom properties.
Không xử lý: chữ nằm trong ảnh, object nhúng (OLE, file Excel của chart) -> báo trong `warnings`.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from ..segments import Replacement, Segment

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
w = lambda tag: f"{{{W}}}{tag}"  # noqa: E731
a = lambda tag: f"{{{A}}}{tag}"  # noqa: E731

# Part có nội dung văn bản
TEXT_PART = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes|comments(Extended)?|glossary/document"
                       r"|charts/chart\d*|diagrams/data\d*|diagrams/drawing\d*)\.xml$")
# Track changes: phần bị xoá -> bỏ hẳn; phần chèn -> gỡ thẻ, giữ nội dung; lịch sử định dạng -> bỏ
TRACK_REMOVE = {w(t) for t in ("del", "moveFrom", "pPrChange", "rPrChange", "sectPrChange", "tblPrChange",
                               "trPrChange", "tcPrChange", "tblGridChange", "tblPrExChange", "numberingChange",
                               "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")}
TRACK_UNWRAP = {w("ins"), w("moveTo")}
AUTHOR_ATTRS = {"author", "initials", "userId", "providerId"}
CORE_CLEAR = {"creator", "lastModifiedBy"}
CORE_MASK = {"title", "subject", "description", "keywords", "category"}
APP_CLEAR = {"Company", "Manager"}
MAX_UNCOMPRESSED = 300 * 1024 * 1024   # chặn zip bomb


class DocxError(ValueError):
    pass


@dataclass
class _Piece:
    start: int
    end: int
    elem: etree._Element
    editable: bool          # w:t / a:t: sửa text; tab/br: chỉ có thể xoá


@dataclass
class _Unit:
    """Một Segment trỏ vào XML: paragraph (danh sách piece), node text đơn, hoặc thuộc tính."""
    kind: str               # "para" | "text" | "attr"
    part: str
    pieces: list[_Piece] = field(default_factory=list)
    elem: etree._Element | None = None
    attr: str | None = None


class DocxDocument:
    def __init__(self, data: bytes):
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise DocxError("không phải file DOCX (zip hỏng)") from e
        names = set(self._zip.namelist())
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise DocxError("không phải file DOCX (thiếu word/document.xml)")
        if sum(i.file_size for i in self._zip.infolist()) > MAX_UNCOMPRESSED:
            raise DocxError("file giải nén quá lớn")
        self._trees: dict[str, etree._ElementTree] = {}
        self._dirty: set[str] = set()
        self._units: dict[str, _Unit] = {}
        self.warnings: list[str] = []
        self._accept_track_changes_and_scrub_authors()
        self._scrub_metadata()
        self._collect()
        self._check_unhandled()

    @classmethod
    def open(cls, path) -> "DocxDocument":
        with open(path, "rb") as f:
            return cls(f.read())

    # ------------------------------------------------------------------ XML I/O
    def _tree(self, name: str) -> etree._ElementTree:
        if name not in self._trees:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=False)
            self._trees[name] = etree.ElementTree(etree.fromstring(self._zip.read(name), parser))
        return self._trees[name]

    def _xml_parts(self, pattern=None):
        for n in self._zip.namelist():
            if (n.endswith(".xml") or n.endswith(".rels")) and (pattern is None or pattern.match(n)):
                yield n

    def save(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for src in self._zip.infolist():
                # ZipInfo mới: writestr() sửa offset/size của ZipInfo truyền vào -> không dùng lại của file nguồn
                info = zipfile.ZipInfo(src.filename, date_time=src.date_time)
                info.external_attr, info.compress_type = src.external_attr, zipfile.ZIP_DEFLATED
                if src.filename in self._dirty:
                    data = etree.tostring(self._trees[src.filename], xml_declaration=True,
                                          encoding="UTF-8", standalone=True)
                else:
                    data = self._zip.read(src.filename)
                z.writestr(info, data)
        return out.getvalue()

    # ------------------------------------------------------------------ dọn trước khi trích
    def _accept_track_changes_and_scrub_authors(self):
        for name in self._xml_parts(re.compile(r"^word/.*\.xml$")):
            root = self._tree(name).getroot()
            changed = False
            for el in list(root.iter(*TRACK_REMOVE)):
                el.getparent().remove(el)
                changed = True
            for el in list(root.iter(*TRACK_UNWRAP)):
                parent, idx = el.getparent(), el.getparent().index(el)
                for child in reversed(list(el)):
                    parent.insert(idx, child)
                parent.remove(el)
                changed = True
            for el in root.iter():
                for k in list(el.attrib):
                    if etree.QName(k).localname in AUTHOR_ATTRS:
                        el.attrib[k] = "Author" if etree.QName(k).localname == "author" else ""
                        changed = True
            if changed:
                self._dirty.add(name)

    def _scrub_metadata(self):
        for name, clear in (("docProps/core.xml", CORE_CLEAR), ("docProps/app.xml", APP_CLEAR)):
            if name not in self._zip.namelist():
                continue
            for el in self._tree(name).getroot():
                if etree.QName(el).localname in clear and (el.text or "").strip():
                    el.text = ""
                    self._dirty.add(name)
        if "docProps/custom.xml" in self._zip.namelist():
            for el in self._tree("docProps/custom.xml").getroot().iter():
                if len(el) == 0 and (el.text or "").strip() and etree.QName(el).localname not in ("filetime", "bool"):
                    el.text = ""
                    self._dirty.add("docProps/custom.xml")

    # ------------------------------------------------------------------ trích segment
    def _add(self, sid: str, unit: _Unit):
        self._units[sid] = unit

    def _collect(self):
        for name in self._xml_parts(TEXT_PART):
            root = self._tree(name).getroot()
            for i, p in enumerate(root.iter(w("p"))):
                self._add(f"{name}#w{i}", _Unit("para", name, pieces=self._w_pieces(p)))
            for i, p in enumerate(root.iter(a("p"))):
                self._add(f"{name}#a{i}", _Unit("para", name, pieces=self._a_pieces(p)))
            for i, el in enumerate(root.iter(w("instrText"))):
                self._add(f"{name}#instr{i}", _Unit("text", name, elem=el))
            for i, el in enumerate(root.iter(w("fldSimple"))):
                self._add(f"{name}#fld{i}", _Unit("attr", name, elem=el, attr=w("instr")))
            for i, el in enumerate(root.iter(f"{{{WP}}}docPr")):
                for at in ("descr", "title"):
                    if el.get(at):
                        self._add(f"{name}#docPr{i}.{at}", _Unit("attr", name, elem=el, attr=at))
        for name in self._xml_parts(re.compile(r"^word/.*\.rels$")):
            for i, el in enumerate(self._tree(name).getroot().iter(f"{{{REL}}}Relationship")):
                if el.get("TargetMode") == "External":
                    self._add(f"{name}#rel{i}", _Unit("attr", name, elem=el, attr="Target"))
        if "docProps/core.xml" in self._zip.namelist():
            for el in self._tree("docProps/core.xml").getroot():
                if etree.QName(el).localname in CORE_MASK and (el.text or "").strip():
                    self._add(f"docProps/core.xml#{etree.QName(el).localname}", _Unit("text", "docProps/core.xml",
                                                                                      elem=el))

    @staticmethod
    def _owner(el, p_tag):
        """Paragraph gần nhất chứa el (textbox lồng trong paragraph được tính là paragraph riêng)."""
        anc = el.getparent()
        while anc is not None and anc.tag != p_tag:
            anc = anc.getparent()
        return anc

    def _w_pieces(self, p):
        pieces, pos = [], 0
        for el in p.iter(w("t"), w("tab"), w("br"), w("cr"), w("noBreakHyphen")):
            if self._owner(el, w("p")) is not p or el.getparent().tag != w("r"):
                continue   # paragraph lồng (textbox) / w:tab trong w:tabs của pPr
            if el.tag == w("t"):
                txt, editable = el.text or "", True
            else:
                txt, editable = ("\t" if el.tag == w("tab") else "-" if el.tag == w("noBreakHyphen") else "\n"), False
            pieces.append(_Piece(pos, pos + len(txt), el, editable))
            pos += len(txt)
        return pieces

    def _a_pieces(self, p):
        pieces, pos = [], 0
        for el in p.iter(a("t"), a("br")):
            if self._owner(el, a("p")) is not p:
                continue
            txt, editable = (el.text or "", True) if el.tag == a("t") else ("\n", False)
            pieces.append(_Piece(pos, pos + len(txt), el, editable))
            pos += len(txt)
        return pieces

    def _check_unhandled(self):
        names = self._zip.namelist()
        media = [n for n in names if n.startswith("word/media/")]
        embeds = [n for n in names if n.startswith("word/embeddings/")]
        if media:
            self.warnings.append(f"{len(media)} ảnh không được kiểm tra: chữ nằm trong ảnh (nếu có) KHÔNG được che")
        if embeds:
            self.warnings.append(f"{len(embeds)} object nhúng (OLE / dữ liệu chart) không được kiểm tra")

    # ------------------------------------------------------------------ API
    def segments(self) -> list[Segment]:
        out = []
        for sid, u in self._units.items():
            if u.kind == "para":
                text = "".join(self._piece_text(pc) for pc in u.pieces)
                meta = dict(part=u.part, kind="para",
                            runs=[dict(start=pc.start, end=pc.end, editable=pc.editable) for pc in u.pieces])
            else:
                text = (u.elem.text if u.kind == "text" else u.elem.get(u.attr)) or ""
                meta = dict(part=u.part, kind=u.kind)
            if text.strip():
                out.append(Segment(sid, text, meta))
        return out

    @staticmethod
    def _piece_text(pc: _Piece) -> str:
        if pc.editable:
            return pc.elem.text or ""
        return {w("tab"): "\t", w("noBreakHyphen"): "-"}.get(pc.elem.tag, "\n")

    def apply(self, replacements: list[Replacement]):
        by_seg: dict[str, list[Replacement]] = {}
        for r in replacements:
            by_seg.setdefault(r.seg, []).append(r)
        for sid, reps in by_seg.items():
            u = self._units[sid]
            reps.sort(key=lambda r: r.start)
            if u.kind == "para":
                self._apply_para(u, reps)
            else:
                text = (u.elem.text if u.kind == "text" else u.elem.get(u.attr)) or ""
                for r in reversed(reps):
                    text = text[:r.start] + self._attr_tag(u, r.tag) + text[r.end:]
                if u.kind == "text":
                    u.elem.text = text
                else:
                    u.elem.set(u.attr, text)
            self._dirty.add(u.part)

    @staticmethod
    def _attr_tag(u: _Unit, tag: str) -> str:
        # Trong URL (Relationship Target) bỏ dấu < > để Word không báo link hỏng
        return tag.strip("<>") if u.attr == "Target" else tag

    @staticmethod
    def _apply_para(u: _Unit, reps: list[Replacement]):
        """Thay từng span bằng tag: tag đặt vào node text ĐẦU TIÊN mà span chạm tới (giữ định dạng của run đó),
        phần còn lại của span bị cắt khỏi các node text sau; tab / xuống dòng nằm trong span bị xoá."""
        anchor = {}
        for i, r in enumerate(reps):
            anchor[i] = next((pc for pc in u.pieces if pc.editable and pc.start < r.end and r.start < pc.end), None)
            if anchor[i] is None:
                raise DocxError(f"không tìm thấy node text cho span {r.start}-{r.end}")
        for pc in u.pieces:
            hits = [(i, r) for i, r in enumerate(reps) if r.start < pc.end and pc.start < r.end]
            if not hits:
                continue
            if not pc.editable:
                pc.elem.getparent().remove(pc.elem)   # tab/br luôn dài 1 ký tự -> nằm trọn trong span
                continue
            text, out, pos = pc.elem.text or "", [], pc.start
            for i, r in hits:
                out.append(text[pos - pc.start:max(r.start, pc.start) - pc.start])
                if anchor[i] is pc:
                    out.append(r.tag)
                pos = min(r.end, pc.end)
            out.append(text[pos - pc.start:])
            pc.elem.text = "".join(out)
            if pc.elem.tag == w("t"):
                pc.elem.set(XML_SPACE, "preserve")

    # ------------------------------------------------------------------ cho leak-check
    def raw_text_nodes(self):
        """(part, chuỗi) cho MỌI node text và giá trị thuộc tính trong mọi part XML - không phụ thuộc extractor."""
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
        for name in self._xml_parts():
            root = self._trees[name].getroot() if name in self._trees else etree.fromstring(self._zip.read(name), parser)
            for el in root.iter():
                if isinstance(el.tag, str):
                    if el.text and el.text.strip():
                        yield name, el.text
                    for v in el.attrib.values():
                        if len(v) > 2:
                            yield name, v
