"""Chính sách che: entity_type -> operator Presidio (replace = che bằng tag, keep = giữ nguyên).

Nhãn trong `new_value` (bỏ <>()) là tên tag mà Pseudonymizer đánh số: "<PERSON>" -> <PERSON_1>, <PERSON_2>...
"""
from dataclasses import dataclass, field

from presidio_anonymizer.entities import OperatorConfig

KEEP = OperatorConfig("keep")


def tag(label: str) -> OperatorConfig:
    return OperatorConfig("replace", {"new_value": f"<{label}>"})


# Chính sách của CC1 / eval (eval/common.py: MASK_LABELS, KEEP_LABELS)
_BCTC = {
    "PERSON": tag("PERSON"),
    "EMAIL_ADDRESS": tag("EMAIL"),
    "PHONE_NUMBER": tag("PHONE"),
    "VN_PHONE_NUMBER": tag("PHONE"),
    "VN_ID_CARD": tag("ID_CARD"),
    "VN_PASSPORT": tag("PASSPORT"),
    "PRACTICE_CERT": tag("PRACTICE_CERT"),
    # Dữ liệu cá nhân -> che
    "VN_ADDRESS_PERSONAL": tag("ADDRESS"),
    "VN_TAX_CODE_PERSONAL": tag("TAX_CODE"),
    "BANK_ACCOUNT_PERSONAL": tag("BANK_ACCOUNT"),
    # Mã số doanh nghiệp & số tài khoản của tổ chức -> cũng che (yêu cầu bổ sung của CC1)
    "VN_TAX_CODE_ORG": tag("TAX_CODE"),
    "BANK_ACCOUNT_ORG": tag("BANK_ACCOUNT"),
    # Địa chỉ tổ chức / địa danh chung -> giữ (vẫn detect để báo cáo)
    "VN_ADDRESS_ORG": KEEP,
    "LOCATION": KEEP,
    # Chưa qua bước phân loại chủ thể (cấu hình A) -> che (thận trọng)
    "VN_ADDRESS": tag("ADDRESS"),
    "VN_TAX_CODE": tag("TAX_CODE"),
    "BANK_ACCOUNT": tag("BANK_ACCOUNT"),
    # Loại của offer letter (demo/doc.py); analyzer D không sinh ra nhưng giữ để chính sách đầy đủ
    "VN_SALARY": tag("SALARY"),
    "EMPLOYEE_ACCOUNT": OperatorConfig("replace", {"new_value": "(<ACCOUNT>)"}),
    "DEFAULT": tag("PII"),
}


@dataclass(frozen=True)
class Policy:
    name: str
    label: str
    description: str
    operators: dict = field(default_factory=dict)

    def operator_for(self, entity_type: str) -> OperatorConfig:
        return self.operators.get(entity_type, self.operators.get("DEFAULT", KEEP))

    def is_masked(self, entity_type: str) -> bool:
        return self.operator_for(entity_type).operator_name != "keep"


POLICIES = {
    "bctc": Policy(
        "bctc", "Báo cáo tài chính (như CC1)",
        "Che dữ liệu cá nhân, cùng MST và số tài khoản của tổ chức. Giữ địa chỉ tổ chức, địa danh, số tiền.",
        _BCTC),
    "personal": Policy(
        "personal", "Chỉ dữ liệu cá nhân",
        "Chỉ che dữ liệu cá nhân. Giữ MST, số tài khoản và địa chỉ của tổ chức, địa danh, số tiền.",
        {**_BCTC, "VN_TAX_CODE_ORG": KEEP, "BANK_ACCOUNT_ORG": KEEP}),
}
DEFAULT_POLICY = "bctc"


def get_policy(name: str | None = None) -> Policy:
    return POLICIES[name or DEFAULT_POLICY]
