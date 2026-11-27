"""Recognizer dạng pattern / gazetteer (gốc: demo/doc.py, dùng cho offer letter và cấu hình D)."""
import re

from presidio_analyzer import Pattern, PatternRecognizer

from ..gazetteer import VN_PLACES


def build_custom_recognizers() -> list[PatternRecognizer]:
    """[vn_phone, salary, account, titled_person, address (EN), gazetteer]. Thứ tự được demo/bench.py dựa vào."""
    vn_phone = PatternRecognizer(
        supported_entity="VN_PHONE_NUMBER",
        patterns=[
            # Di động: 0xxxxxxxxx (10 số)
            Pattern("vn_mobile", r"(?<![\d.,])0[35789]\d{8}(?![\d.,]?\d)", 0.7),
            # Cố định: 02xx xxx xxxx / 02xxxxxxxxx
            Pattern("vn_landline", r"(?<![\d.,])02\d{2}[\s.]?\d{3}[\s.]?\d{3,4}(?![\d.,]?\d)", 0.6),
            # Có mã quốc gia: +84 / 84 + dấu cách (84 24 3768 9048). Bắt buộc có phân cách sau "84"
            # để không bắt nhầm số tiền viết liền như 848974976277
            Pattern("vn_intl", r"(?<![\d.,])(?:\+84[\s.]?|84[\s.])\d{2}[\s.]?\d{4}[\s.]?\d{4}(?![\d.,]?\d)", 0.6),
        ],
        context=["tel", "phone", "cellphone", "fax", "mobile", "sđt"],
    )

    salary = PatternRecognizer(
        supported_entity="VN_SALARY",
        patterns=[Pattern("vnd_amount", r"\b\d{1,3}(?:[,.]\d{3})+\s?(?:VND|VNĐ|đ)\b", 0.85)],
        context=["salary", "lương", "pay"],
    )

    # Account nội bộ kiểu FPT: TrungND2, PhuPTN ...
    account = PatternRecognizer(
        supported_entity="EMPLOYEE_ACCOUNT",
        patterns=[Pattern("fpt_account", r"\(([A-Z][a-z]+[A-Z]{2,4}\d*)\)", 0.6)],
        global_regex_flags=re.DOTALL | re.MULTILINE,  # phân biệt hoa/thường, tránh bắt "(viii)"
    )

    # Tên người có danh xưng: "Mr. Pham Tran Nguyen Phu" — spaCy hay bỏ sót tên Việt
    titled_person = PatternRecognizer(
        supported_entity="PERSON",
        name="TitledPersonRecognizer",
        patterns=[
            Pattern(
                "titled_name",
                r"(?<=\b(?:Mr|Ms|Mrs|Miss)\.\s)(?:[A-Z][a-z]+\s){1,4}[A-Z][a-z]+",
                0.85,
            )
        ],
        global_regex_flags=re.DOTALL | re.MULTILINE,  # phân biệt hoa/thường
    )
    return [vn_phone, salary, account, titled_person, build_address_recognizer(), build_vn_place_recognizer()]


# spaCy hay nhận tên đường VN (đặt theo tên người) thành PERSON -> bắt cụm địa chỉ tiếng Anh thành LOCATION
ADDRESS_SUFFIX = (
    r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Lane|Alley|Highway|"
    r"Ward|District|Commune|Town|City|Province|Building|Tower|Complex|Park)"
)
# 1-6 từ viết hoa (có thể xuống dòng, vd "Tang \nNhon Phu Ward") + hậu tố địa chỉ
ADDRESS_REGEX = rf"\b(?:[A-Z][a-z]+ ?\n?[ \t]?){{1,6}}{ADDRESS_SUFFIX}(?![A-Za-z])"
CASE_SENSITIVE = re.DOTALL | re.MULTILINE


def build_address_recognizer() -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity="LOCATION",
        name="VnAddressRecognizer",
        patterns=[Pattern("vn_address", ADDRESS_REGEX, 0.9)],
        global_regex_flags=CASE_SENSITIVE,  # mặc định Presidio dùng IGNORECASE -> [A-Z] khớp cả chữ thường
    )


def build_vn_place_recognizer() -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity="LOCATION",
        name="VnPlaceGazetteer",
        deny_list=VN_PLACES,
        deny_list_score=0.9,
        global_regex_flags=CASE_SENSITIVE,  # tránh "hue" thường bị bắt thành địa danh Huế
    )
