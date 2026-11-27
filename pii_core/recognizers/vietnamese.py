"""Recognizer luật cho văn bản tiếng Việt (gốc: cc1/run_cc1.py): tên người, khối địa chỉ, SĐT, mã định danh, MST."""
import re

import regex  # hỗ trợ \p{Lu} (chữ hoa Unicode) cho tiếng Việt có dấu
from presidio_analyzer import AnalysisExplanation, EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult

from ..gazetteer import HONORIFICS, NON_PERSON_PREV, VN_PLACES, VN_SURNAMES


class VnSurnameNameRecognizer(EntityRecognizer):
    """
    Tên người Việt dạng trần (không có Ông/Bà), vd trong bảng: <td>Lê Bảo Anh</td>.
    Luật: bắt đầu bằng họ phổ biến + 1-4 âm tiết viết hoa, và
      - từ đứng trước không phải số nhà / từ chỉ địa chỉ (79 Lê Thị Riêng, Phố Phùng Chí Kiên)
      - từ đứng trước không phải từ viết hoa khác (=> là đuôi tên công ty: Hoa Hướng Dương Sài Gòn),
        trừ khi đó là danh xưng Ông/Bà.
    Địa danh trùng họ (Hà Nội, Thái Bình, Lâm Đồng...) bị loại ở resolve_conflicts nhờ gazetteer.
    """
    WORD = re.compile(r"[^\s<>,.;:()\[\]\"/]+")

    def __init__(self):
        super().__init__(supported_entities=["PERSON"], name="VnSurnameNameRecognizer",
                         supported_language="en")

    def load(self):
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        words = [(m.group(0), m.start(), m.end()) for m in self.WORD.finditer(text)]
        results, i = [], 0
        while i < len(words):
            w, s, _ = words[i]
            if w not in VN_SURNAMES:
                i += 1
                continue
            j = i + 1
            # nối các âm tiết viết hoa liền kề (cách nhau đúng 1 dấu cách)
            # (bỏ từ VIẾT HOA TOÀN BỘ như "VND", "CTCP" -> tránh "Tăng VND")
            while j < len(words) and j - i < 5 and words[j][0][:1].isupper() and words[j][0].isalpha() \
                    and not words[j][0].isupper() and len(words[j][0]) > 1 \
                    and text[words[j - 1][2]:words[j][1]] == " ":
                j += 1
            if j - i >= 2:
                prev = words[i - 1][0] if i > 0 and text[words[i - 1][2]:s].strip() in ("", "-") else ""
                prev_l = prev.lower()
                # "–194 Trần Quang Khải": số nhà có gạch nối đứng trước vẫn là số nhà -> tên đường
                bad = (prev.lstrip("–—-")[:1].isdigit() or prev_l in NON_PERSON_PREV
                       or (prev[:1].isupper() and prev_l not in HONORIFICS))
                if not bad:
                    in_cell = text[max(0, s - 6):s].rstrip("- ").endswith(">")
                    score = 0.85 if prev_l in HONORIFICS or in_cell else 0.6
                    results.append(RecognizerResult(
                        "PERSON", s, words[j - 1][2], score,
                        recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: self.name}))
                i = j
            else:
                i += 1
        return results


class VnAddressBlockRecognizer(EntityRecognizer):
    """
    Cụm địa chỉ cấp đường/phường -> VN_ADDRESS, NỐI THÊM đuôi tỉnh/thành (+ "Việt Nam") nếu đứng liền sau
    (theo eval/README.md: địa chỉ gồm cả tỉnh/thành). Tỉnh/thành đứng riêng vẫn là LOCATION (giữ).
      "số 111A, Đường Pasteur, Phường Bến Nghé, Quận 1"  | "79 Lê Thị Riêng, Phường 1"
      "Thửa đất số 93, tờ bản đồ số 76, Đường Tỉnh 8, Ấp Hội Trung, ..." | "Lô 185 Phòng 1508, Tầng 15"
    Không bắt: "Thành phố Hồ Chí Minh", "Sở GDCK Hà Nội", "Công ty TNHH Đầu tư Đường Ven Biển Hải Phòng".
    """
    # 1 từ của tên: viết hoa hoặc số; không nuốt sang cấp tỉnh/thành (TP, Thành phố, Tỉnh, Việt Nam)
    # ("Tỉnh 8" = đường tỉnh lộ thì vẫn nhận; "Lê Thành Phương" vẫn nhận, chỉ chặn "Thành phố")
    W = (r"(?!(?:TP|Tp|Việt Nam|Thành [Pp]hố|Tỉnh(?! \d))(?!\p{L}))"   # "Việt Nam" chặn, "Hoàng Quốc Việt" vẫn nhận
         r"(?:\p{Lu}[\p{L}\p{N}'’-]*(?:\.[\p{L}\p{N}]+)*|\d+[A-Z]?)")  # dấu chấm chỉ ở giữa từ, không nuốt dấu chấm câu
    NAME = rf"{W}(?: {W}){{0,5}}"
    KW = (r"(?:(?<!(?:Thành|thành|Khu|khu) )(?:Đường|đường|Phố|phố)|Ngõ|ngõ|Ngách|ngách|Hẻm|hẻm|"
          r"Phường|phường|Xã|xã|Thị trấn|thị trấn|Quận|quận|Huyện|huyện|Thị xã|thị xã|Ấp|ấp|Thôn|thôn|"
          r"Khu phố|khu phố|Tổ dân phố|tổ dân phố|Xóm|xóm|Thửa đất số|thửa đất số|Tờ bản đồ số|tờ bản đồ số)")
    # chỉ khi theo sau là số (tránh "Phòng Kế toán"); "văn phòng 209A ..." -> "phòng" KHÔNG phải số phòng
    NUMKW = r"(?:(?<![Vv]ăn )(?:Phòng|phòng)|Tầng|tầng|Lô|lô|Căn hộ|căn hộ) \p{Lu}?\d[\w.-]*"
    COMP = rf"(?:(?<!\p{{L}}){KW} {NAME}|(?<!\p{{L}}){NUMKW})"
    HOUSE = r"(?<![\d.,/])(?:[Ss]ố )?\d{1,4}[A-Z]?(?:/\d{1,4}[A-Z]?)*"
    # 1 tên trần (tòa nhà / chung cư: "Sunrise City") được phép đứng giữa nếu ngay sau là thành phần địa chỉ
    BARE = rf"(?:, {NAME}(?=,? {COMP}))?"
    # Cụm bắt đầu bằng từ khóa mà đứng sau 1 từ viết hoa => là đuôi tên riêng (tên công ty) -> bỏ
    PATTERN = regex.compile(
        rf"{HOUSE}(?: {NAME})?{BARE}(?:,? {COMP})+|(?<!\p{{Lu}}\p{{L}}* ){COMP}{BARE}(?:,? {COMP})*")
    # Đuôi cấp tỉnh/thành (+ quốc gia) nối vào cụm: theo hướng dẫn gán nhãn, địa chỉ gồm cả tỉnh/thành.
    # Chỉ nối khi có tiền tố hành chính ("tỉnh X", "thành phố X", "TP. X") hoặc tên nằm trong gazetteer.
    _PLACES = "|".join(sorted({regex.escape(p) for p in VN_PLACES}, key=len, reverse=True))
    TAIL = regex.compile(
        rf"(?:,? (?:(?:[Tt]ỉnh|[Tt]hành phố|TP\.|Tp\.|TP) ?{NAME}|TP\.? ?HCM|Tp\.? ?HCM|(?:{_PLACES})(?!\p{{L}})))"
        rf"{{1,3}}(?:,? Việt Nam(?!\p{{L}}))?")

    def __init__(self):
        super().__init__(supported_entities=["VN_ADDRESS"], name="VnAddressBlockRecognizer",
                         supported_language="en")

    def load(self):
        pass

    # Từ chỉ tổ chức đứng ngay trước => cụm phía sau là tên tổ chức ("Công ty TNHH Đầu tư Đường Ven Biển...")
    ORG_BEFORE = regex.compile(r"(?i)(công ty|tnhh|cổ phần|ctcp|tập đoàn|ngân hàng|dự án|ban quản lý|đầu tư)"
                               r"[^,;:.\n<>()]{0,25}$")
    ADDRESS_INTRO = regex.compile(r"(?i)(tại|ở|địa chỉ|trụ sở)\s*:?\s*$")

    def analyze(self, text, entities, nlp_artifacts=None):
        results = []
        for m in self.PATTERN.finditer(text):
            before = text[max(0, m.start() - 60):m.start()]
            starts_with_house = m.group(0)[:1].isdigit() or m.group(0)[:3].lower() == "số "
            if not starts_with_house and self.ORG_BEFORE.search(before) and not self.ADDRESS_INTRO.search(before):
                continue
            tail = self.TAIL.match(text, m.end())
            end = tail.end() if tail else m.end()
            results.append(RecognizerResult(
                "VN_ADDRESS", m.start(), end, 0.9,
                recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: self.name}))
        return results


class PrefixedIdRecognizer(EntityRecognizer):
    """
    Mã định danh chỉ nhận khi có TỪ KHÓA đứng trước (tránh nhầm với số tiền / mã chỉ tiêu):
      "CCCD số 079085001234", "hộ chiếu: C1234567", "số tài khoản 0071000123456", ...
    """
    SEP = r"[\s:]*(?:số|no\.?|number)?[\s:.]*"

    def __init__(self, entity, name, prefix, value, score=0.95):
        super().__init__(supported_entities=[entity], name=name, supported_language="en")
        self.entity, self.score = entity, score
        self.pattern = regex.compile(rf"(?<![\p{{L}}])(?i:{prefix}){self.SEP}(?P<v>{value})(?![\w-])")

    def load(self):
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        return [RecognizerResult(self.entity, m.start("v"), m.end("v"), self.score,
                                 recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: self.name})
                for m in self.pattern.finditer(text)]


class VnPhoneRecognizer(EntityRecognizer):
    """
    SĐT Việt Nam, nhiều kiểu viết - thay cho 3 pattern cứng trong build_custom_recognizers().
      Di động : 0 + [35789]x + 7 số  (10 số)       0912345678 · 0912 345 678 · 098.765.4321 · 0903-456-789
      Cố định : 0 + 2xx/2x + 7-8 số (11 số)       028 3822 1234 · (024) 5594 0781 · 0292.1171.822 · 024. 7300 7300
      Quốc tế : +84 / (+84) / 0084, có thể kèm (0)  +84 903 456 789 · (+84) 370 812 191 · +84 (0) 28 3822 1234
    Bước 1: regex ứng viên (chỉ chữ số + phân cách ' ', '.', '-', ngoặc quanh mã vùng).
    Bước 2: kiểm tra SỐ CHỮ SỐ theo đầu số + mỗi nhóm >= 2 số -> loại số tiền "0.987.654.321",
            "84.912.345.678" (không có '+'), mã chỉ tiêu, số CCCD 12 số...
    """
    SEP = r"[ .\-]"
    CAND = regex.compile(
        r"(?<![\w+.,/\-])"
        r"(?:(?P<cc>\(\+84\)|\+84|0084)[ .\-]?(?:\(0\)[ .\-]?)?)?"
        r"(?P<body>\(0?\d{2,4}\)[ .\-]?\d{2,5}(?:[ .\-]?\d{2,5}){0,3}"
        # mã vùng + ". " (dấu chấm rồi dấu cách): "024. 7300 7300" - chỉ khi nhóm đầu là mã vùng bắt đầu bằng 0,
        # để không nuốt chuỗi kiểu "số 12. 0283 822 1234"
        r"|0\d{2,3}\. \d{3,4}(?:[ .\-]?\d{3,4}){1,2}"
        r"|\d{2,5}(?:[ .\-]?\d{2,5}){0,4})"
        r"(?![\w]|[.,\-/]\d)")
    CONTEXT = ["điện thoại", "đt", "sđt", "di động", "liên hệ", "hotline", "phone", "tel", "fax", "mobile", "zalo"]
    # MST 10 số (03x/05x/07x/08x/09x...) & số tài khoản trùng dạng di động -> nhường nếu có từ gợi ý gần ngay trước
    NOT_PHONE_CUE = regex.compile(
        r"(?i)(?:mã số thuế|MST|mã số doanh nghiệp|đăng ký doanh nghiệp|MSDN|M\.S\.D\.N|tài khoản|STK|TK|"
        r"CCCD|CMND|căn cước|định danh|hộ chiếu)[^\n\d]{0,12}$")
    PHONE_CUE = regex.compile(r"(?i)(?:điện thoại|ĐT|SĐT|di động|hotline|phone|tel|fax|zalo|gọi)[^\n\d]{0,25}$")

    def __init__(self):
        super().__init__(supported_entities=["VN_PHONE_NUMBER"], name="VnPhoneRecognizer",
                         supported_language="en", context=self.CONTEXT)

    def load(self):
        pass

    @staticmethod
    def _national(cc, body):
        groups = regex.findall(r"\d+", body)
        if any(len(g) < 2 for g in groups[1:]) or (len(groups) > 1 and len(groups[0]) < 2):
            return None
        digits = "".join(groups)
        if cc:
            digits = digits[1:] if digits.startswith("0") else digits   # +84 (0)28... / +84 028...
            return "0" + digits if not digits.startswith("0") else None
        return digits if digits.startswith("0") else None

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        for m in self.CAND.finditer(text):
            nat = self._national(m.group("cc"), m.group("body"))
            if not nat:
                continue
            if regex.fullmatch(r"0[35789]\d{8}", nat):
                score = 0.75
            elif regex.fullmatch(r"02\d{9}", nat):
                score = 0.65
            else:
                continue
            start = m.start("cc") if m.group("cc") else m.start("body")
            before = text[max(0, start - 40):start]
            if self.NOT_PHONE_CUE.search(before) and not self.PHONE_CUE.search(before):
                continue
            out.append(RecognizerResult(
                "VN_PHONE_NUMBER", start, m.end(), score,
                analysis_explanation=AnalysisExplanation(recognizer=self.name, original_score=score),  # cần cho context enhancer
                recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: self.name}))
        return out


def build_identifier_recognizers():
    """Mã định danh cá nhân (spec mục 6)."""
    return [
        PrefixedIdRecognizer(
            "VN_ID_CARD", "VnIdCardRecognizer",
            r"CCCD|CMND|CMTND|căn cước(?: công dân)?|chứng minh (?:nhân dân|thư)|số định danh(?: cá nhân)?",
            r"\d{12}|\d{9}"),
        PrefixedIdRecognizer("VN_PASSPORT", "VnPassportRecognizer", r"hộ chiếu|passport",
                             r"[A-Z]\d{7,8}"),
        PrefixedIdRecognizer(
            "BANK_ACCOUNT", "BankAccountRecognizer",
            r"số tài khoản(?: ngân hàng)?|tài khoản(?: ngân hàng)? số|STK|TK số|account (?:no|number)",
            r"\d{3,4}(?:[ -]\d{3,4}){2,5}|\d{6,19}"),
        PrefixedIdRecognizer(
            "PRACTICE_CERT", "PracticeCertRecognizer",
            r"chứng chỉ (?:hành nghề(?: kế toán| kiểm toán)?|kiểm toán viên|KTV)|"
            r"giấy chứng nhận đăng ký hành nghề(?: kiểm toán)?|giấy CN ĐKHN(?: kiểm toán)?|CCHN|ĐKHN",
            r"[A-Z0-9][A-Z0-9/.-]{1,23}[A-Z0-9]"),  # kết thúc bằng chữ/số (không dính dấu chấm câu)
        # Định dạng giấy CN đăng ký hành nghề kiểm toán: NNNN-YYYY-NNN-N (không cần từ khóa)
        PatternRecognizer(supported_entity="PRACTICE_CERT", name="PracticeCertFormat",
                          patterns=[Pattern("dkhn", r"(?<![\d-])\d{4}-\d{4}-\d{3}-\d(?![\d-])", 0.85)]),
        # MST cá nhân (10 số, hoặc 12 số = số CCCD từ 07/2025)
        PrefixedIdRecognizer("VN_TAX_CODE", "VnPersonalTaxRecognizer",
                             r"mã số thuế cá nhân|MST cá nhân|mã số thuế TNCN|MST TNCN", r"\d{12}|\d{10}"),
    ]


def build_vn_extra_recognizers():
    tax = PatternRecognizer(
        supported_entity="VN_TAX_CODE",
        name="VnTaxCodeRecognizer",
        patterns=[
            Pattern("tax_prefixed",
                    r"(?:(?<=số )|(?<=MST: )|(?<=MST )|(?<=M\.S\.D\.N: )|(?<=MSDN: )|(?<=thuế: ))"
                    r"\d{10}(?:-\d{3})?\b", 0.9),
            Pattern("tax_generic", r"(?<![\d.,])\d{10}(?:-\d{3})?(?![\d.,])", 0.3),
        ],
        context=["thuế", "mst", "msdn", "m.s.d.n", "doanh", "nghiệp"],
    )
    signature = PatternRecognizer(
        supported_entity="PERSON",
        name="SignatureRecognizer",
        patterns=[Pattern("signature", r"(?<=<signature>)[^<\[\d\n]{3,40}(?=</signature>)", 0.85)],
    )
    return ([tax, signature, VnSurnameNameRecognizer(), VnAddressBlockRecognizer()]
            + build_identifier_recognizers())
