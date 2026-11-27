"""Recognizer tiếng Việt có dấu dựa trên pyvi (tách từ + POS) và từ gợi ý (gốc: demo/doc.py)."""
import re

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult


class PyviVietnameseRecognizer(EntityRecognizer):
    """
    Recognizer cho text tiếng Việt CÓ DẤU, dùng pyvi (tách từ + POS).
    pyvi không có NER nên ta dựa vào từ gợi ý đứng trước cụm danh từ riêng (Np):
      - "đường / phường / quận / thành_phố ..." + Np  -> LOCATION
      - "ông / bà / anh / chị ..."              + Np  -> PERSON
    Text không dấu (như offer letter tiếng Anh) sẽ được bỏ qua.
    """

    LOC_CUES = {"đường", "phố", "phường", "quận", "huyện", "xã", "tỉnh", "thành_phố",
                "thị_xã", "thị_trấn", "tp", "tp.", "ấp", "thôn", "ngõ", "ngách", "hẻm", "tòa_nhà"}
    PER_CUES = {"ông", "bà", "anh", "chị", "cô", "chú", "bác", "em", "mr.", "ms.", "mrs."}
    # Từ không bao giờ nằm trong tên riêng -> dừng gom ("CÔ ĐÔNG CỦA CÔNG TY" không kéo sang "CỦA CÔNG TY")
    STOP = {"của", "và", "với", "tại", "là", "cho", "công_ty", "cổ_phần", "tnhh", "tập_đoàn", "ngân_hàng",
            # chức danh đứng ngay sau tên: "Ông X Chức vụ: ..." (thiếu dấu gạch giữa tên và chức danh)
            "chức_vụ", "chức", "giám_đốc", "tổng_giám_đốc", "phó", "trưởng", "chủ_tịch", "thành_viên",
            "kế_toán", "chuyên_viên", "cán_bộ", "trưởng_phòng", "trưởng_ban"}
    # "các / những" + danh xưng = cách gọi chung ("GỬI CÁC CỔ ĐÔNG", OCR hay đọc thành "CÔ ĐÔNG")
    PLURAL = {"các", "những"}
    VN_DIACRITICS = re.compile(r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I)

    def __init__(self):
        super().__init__(supported_entities=["LOCATION", "PERSON"], name="PyviVietnameseRecognizer",
                         supported_language="en")

    def load(self) -> None:
        from pyvi import ViPosTagger, ViTokenizer  # import lười: chỉ load khi cần
        self._tok, self._pos = ViTokenizer, ViPosTagger

    @staticmethod
    def _align(text: str, tokens: list[str]):
        """Map token pyvi (vd 'Hà_Nội') về offset ký tự trong text gốc."""
        spans, cursor = [], 0
        for tok in tokens:
            start, p = None, cursor
            for part in tok.split("_"):
                i = text.find(part, p)
                if i < 0:
                    break
                start = i if start is None else start
                p = i + len(part)
            else:
                spans.append((start, p))
                cursor = p
                continue
            spans.append(None)
        return spans

    # Chỉ chạy pyvi trên những dòng có từ gợi ý -> nhanh hơn nhiều với văn bản dài
    CUE_LINE = re.compile(
        r"(?i)(?<![\w])(đường|phố|phường|quận|huyện|xã|tỉnh|thành phố|thị xã|thị trấn|tp\.?|ấp|thôn|"
        r"ngõ|ngách|hẻm|tòa nhà|ông|bà|anh|chị|cô|chú|bác|em|mr\.|ms\.|mrs\.)(?![\w])")

    def analyze(self, text, entities, nlp_artifacts=None):
        if not self.VN_DIACRITICS.search(text):
            return []
        results, offset = [], 0
        for line in text.splitlines(keepends=True):
            if self.CUE_LINE.search(line):
                for r in self._analyze_line(line, entities):
                    r.start += offset
                    r.end += offset
                    results.append(r)
            offset += len(line)
        return results

    def _analyze_line(self, text, entities):
        tokens, tags = self._pos.postagging(self._tok.tokenize(text))
        spans = self._align(text, tokens)
        results, i = [], 0
        while i < len(tokens):
            cue = tokens[i].lower()
            entity = "LOCATION" if cue in self.LOC_CUES else "PERSON" if cue in self.PER_CUES else None
            if entity is None:
                i += 1
                continue
            j = i + 1
            # gom các token danh từ riêng / viết hoa liền sau từ gợi ý
            while j < len(tokens) and spans[j] and (tags[j] == "Np" or tokens[j][:1].isupper()) \
                    and tags[j] not in ("F", "M") and tokens[j].lower() not in self.STOP:
                j += 1
            generic = (entity == "PERSON" and i > 0 and tokens[i - 1].lower() in self.PLURAL
                       and all(t.isupper() for t in tokens[i + 1:j]))
            if j > i + 1 and entity in entities and spans[i + 1] and not generic \
                    and not (j == i + 2 and tokens[i + 1].lower() in self.LOC_CUES | self.PER_CUES):
                results.append(RecognizerResult(
                    entity, spans[i + 1][0], spans[j - 1][1], 0.9,
                    analysis_explanation=AnalysisExplanation(self.name, 0.9,
                                                             textual_explanation=f"pyvi cue '{cue}'"),
                    recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: self.name},
                ))
            i = j if j > i + 1 else i + 1
        return results
