"""Dựng analyzer: text -> list[RecognizerResult] (đã giải xung đột, đã phân loại chủ thể).

Cấu hình:
  A. Baseline       : Presidio nguyên bản - spaCy en_core_web_lg + recognizer mặc định (chỉ để so sánh)
  D. VN rule-based  : NLP engine chỉ tách từ (spacy.blank), bỏ NER spaCy & PhoneRecognizer mặc định;
                      + recognizer luật / từ điển / pyvi cho tiếng Việt. Không train model nào.
"""
from typing import Callable

import spacy
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_analyzer.predefined_recognizers import EmailRecognizer
from spacy.language import Language

from .postprocess import classify_owner, drop_overlapping_address, resolve_conflicts
from .recognizers import (
    PyviVietnameseRecognizer,
    VnPhoneRecognizer,
    build_custom_recognizers,
    build_vn_extra_recognizers,
)

AnalyzeFn = Callable[[str], list[RecognizerResult]]

ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "VN_PHONE_NUMBER", "LOCATION",
            "VN_TAX_CODE", "VN_ADDRESS", "VN_ID_CARD", "VN_PASSPORT", "BANK_ACCOUNT", "PRACTICE_CERT"]
SCORE_THRESHOLD = 0.4


@Language.component("lower_lemma")
def lower_lemma(d):
    for t in d:
        t.lemma_ = t.lower_  # Presidio dùng lemma cho context-enhancement
    return d


def tokenizer_only_engine() -> SpacyNlpEngine:
    nlp = spacy.blank("en")
    nlp.add_pipe("lower_lemma")
    nlp.max_length = 5_000_000
    eng = SpacyNlpEngine()
    eng.nlp = {"en": nlp}
    return eng


def build_config_A() -> AnalyzeFn:
    analyzer = AnalyzerEngine(supported_languages=["en"])  # spaCy en_core_web_lg mặc định
    analyzer.nlp_engine.nlp["en"].max_length = 5_000_000
    return lambda text: analyzer.analyze(text=text, language="en", entities=ENTITIES,
                                         score_threshold=SCORE_THRESHOLD)


class OrderedRegistry(RecognizerRegistry):
    """RecognizerRegistry gốc gom recognizer qua `set` -> thứ tự đổi giữa các lần chạy. Khi 2 recognizer bắt
    cùng span, cùng score (vd pyvi & gazetteer cùng bắt "Hồ Chí Minh"), Presidio giữ bản đến trước, nên
    danh sách detection không lặp lại được. Ở đây trả recognizer theo đúng thứ tự đăng ký."""

    def get_recognizers(self, *args, **kwargs):
        order = {id(r): i for i, r in enumerate(self.recognizers)}
        return sorted(super().get_recognizers(*args, **kwargs), key=lambda r: order.get(id(r), len(order)))


def build_config_D() -> AnalyzeFn:
    reg = OrderedRegistry(supported_languages=["en"])
    for r in build_custom_recognizers():  # salary/account/titled + address + gazetteer
        if r.supported_entities != ["VN_PHONE_NUMBER"]:  # SĐT: thay bằng VnPhoneRecognizer (đủ kiểu viết)
            reg.add_recognizer(r)
    reg.add_recognizer(VnPhoneRecognizer())
    reg.add_recognizer(PyviVietnameseRecognizer())
    reg.add_recognizer(EmailRecognizer())
    for r in build_vn_extra_recognizers():
        reg.add_recognizer(r)
    analyzer = AnalyzerEngine(nlp_engine=tokenizer_only_engine(), registry=reg, supported_languages=["en"])

    def f(text):
        res = analyzer.analyze(text=text, language="en", entities=ENTITIES, score_threshold=SCORE_THRESHOLD)
        return classify_owner(drop_overlapping_address(resolve_conflicts(res, text)), text)
    return f


CONFIGS = {"A": ("A. Baseline", build_config_A), "D": ("D. VN rule-based", build_config_D)}


def build_analyzer(config: str = "D") -> AnalyzeFn:
    return CONFIGS[config.upper()][1]()
