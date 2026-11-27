"""Hậu xử lý kết quả analyzer: giải xung đột, địa chỉ nuốt tên/địa danh bên trong, phân loại chủ thể."""
import regex
from presidio_analyzer import RecognizerResult

# Recognizer mà kết quả LOCATION của nó được tin hơn PERSON từ spaCy
TRUSTED_LOCATION_SOURCES = {"VnAddressRecognizer", "VnPlaceGazetteer", "PyviVietnameseRecognizer"}


def source(r) -> str:
    """Tên recognizer đã sinh ra kết quả."""
    return (getattr(r, "recognition_metadata", None) or {}).get(RecognizerResult.RECOGNIZER_NAME_KEY, "")


def resolve_conflicts(results: list[RecognizerResult], text: str) -> list[RecognizerResult]:
    """
    1) Bỏ PERSON (của spaCy) nếu đè lên LOCATION từ recognizer địa chỉ / gazetteer / pyvi.
       PERSON có danh xưng Mr./Ms. (TitledPersonRecognizer) được giữ nguyên.
    2) Bỏ PERSON của spaCy có từ viết thường (vd 'việc tại') hoặc đè lên PERSON của pyvi.
    3) Bỏ span nằm gọn trong span khác có score >= (dedupe PHONE vs VN_PHONE, ...).
    """
    def overlaps(a, b):
        return a.start < b.end and b.start < a.end

    trusted_locs = [r for r in results if r.entity_type == "LOCATION" and source(r) in TRUSTED_LOCATION_SOURCES]
    pyvi_persons = [r for r in results if r.entity_type == "PERSON" and source(r) == "PyviVietnameseRecognizer"]

    kept = []
    for r in results:
        if r.entity_type == "PERSON" and source(r) == "SpacyRecognizer":
            words = text[r.start:r.end].split()
            if any(w[:1].islower() for w in words) \
                    or any(overlaps(r, l) for l in trusted_locs) \
                    or any(overlaps(r, p) for p in pyvi_persons):
                continue
        # PERSON đè lên địa danh (vd pyvi bắt "Bà Rịa" -> PERSON "Rịa") -> bỏ, trừ tên có Mr./Ms.
        if r.entity_type == "PERSON" and source(r) not in ("TitledPersonRecognizer",) \
                and any(overlaps(r, l) for l in trusted_locs):
            continue
        kept.append(r)

    final = []
    for r in kept:
        if any(o is not r and o.start <= r.start and r.end <= o.end
               and (o.end - o.start > r.end - r.start or o.score > r.score
                    or (o.score == r.score and kept.index(o) < kept.index(r)))
               for o in kept):
            continue
        final.append(r)
    return sorted(final, key=lambda r: r.start)


def drop_overlapping_address(results):
    """LOCATION/PERSON chồng lên cụm VN_ADDRESS (vd 'Pasteur', 'Lê Thị Riêng') -> để ADDRESS xử lý cả cụm."""
    addrs = [r for r in results if r.entity_type == "VN_ADDRESS"]
    return [r for r in results
            if r.entity_type not in ("LOCATION", "PERSON")
            or not any(r.start < a.end and a.start < r.end for a in addrs)]


# --------------------------------------------------------------------------- #
# Phân loại chủ thể: CÁ NHÂN hay TỔ CHỨC cho địa chỉ / MST / số tài khoản
# --------------------------------------------------------------------------- #
OWNER_TYPED = {"VN_ADDRESS", "VN_TAX_CODE", "BANK_ACCOUNT"}
PERSONAL_CUE = regex.compile(
    r"(?i)(?<![\p{L}])(thường trú|tạm trú|nơi ở|chỗ ở|cư trú|địa chỉ liên hệ|địa chỉ nhà|nhà riêng|"
    r"quê quán|nguyên quán|cá nhân|TNCN|CCCD|CMND|căn cước|hộ chiếu|ông|bà|anh|chị)(?![\p{L}])")
ORG_CUE = regex.compile(
    r"(?i)(?<![\p{L}])(trụ sở|chi nhánh|văn phòng|công ty|CTCP|TNHH|doanh nghiệp|M\.?S\.?D\.?N|"
    r"nhà máy|dự án|tòa nhà|cao ốc|thửa đất|ngân hàng|tập đoàn|đơn vị|tổ chức)(?![\p{L}])")
OWNER_WINDOW = 120


def classify_owner(results, text):
    """
    Với VN_ADDRESS / VN_TAX_CODE / BANK_ACCOUNT: xét từ gợi ý GẦN NHẤT trong 120 ký tự phía trước.
    Gợi ý cá nhân (thường trú, Ông/Bà, CCCD, tên người đã detect...) gần hơn -> *_PERSONAL
    Gợi ý tổ chức (trụ sở, chi nhánh, công ty, đăng ký doanh nghiệp...) gần hơn hoặc không có gì -> *_ORG
    (che hay giữ từng loại là việc của policy, xem pii_core/policy.py)
    """
    persons = [r for r in results if r.entity_type == "PERSON"]
    # từ gợi ý nằm TRONG địa danh đã nhận diện ("Bà" của "Bà Rịa") không phải gợi ý cá nhân
    places = [(r.start, r.end) for r in results if r.entity_type == "LOCATION"]
    for r in results:
        if r.entity_type not in OWNER_TYPED:
            continue
        w0 = max(0, r.start - OWNER_WINDOW)
        window = text[w0:r.start]
        last_personal = max([m.end() for m in PERSONAL_CUE.finditer(window)
                             if not any(s <= w0 + m.start() < e for s, e in places)]
                            + [p.end - w0 for p in persons if w0 <= p.end <= r.start], default=-1)
        last_org = max([m.end() for m in ORG_CUE.finditer(window)], default=-1)
        r.entity_type += "_PERSONAL" if last_personal > last_org else "_ORG"
    return results
