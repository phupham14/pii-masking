"""Che có đánh số + bảng mapping (Presidio không tự tạo mapping)."""
import re
from collections import Counter

from presidio_anonymizer.entities import OperatorConfig

from .policy import Policy, get_policy


def tag_label(cfg: OperatorConfig) -> str:
    """'<PERSON>' -> 'PERSON', '(<ACCOUNT>)' -> 'ACCOUNT'."""
    return cfg.params["new_value"].strip("<>()")


class Pseudonymizer:
    """Mọi operator `replace` của policy được đổi thành nhãn đánh số: `<PERSON>` -> `<PERSON_1>`, `<PERSON_2>`...
    Trong phạm vi một Pseudonymizer (một tài liệu), cùng một giá trị luôn nhận cùng một nhãn
    (so khớp sau khi chuẩn hoá khoảng trắng). Operator `keep` giữ nguyên.
    """
    TAG_RE = re.compile(r"<([A-Z][A-Z_]*_\d+)>")

    def __init__(self):
        self.forward = {}          # (label, giá trị đã chuẩn hoá) -> tag
        self.reverse = {}          # tag -> giá trị gốc (lần xuất hiện đầu tiên)
        self.counter = Counter()

    def tag(self, label: str, original: str) -> str:
        key = (label, " ".join(original.split()))
        if key not in self.forward:
            self.counter[label] += 1
            t = f"<{label}_{self.counter[label]}>"
            self.forward[key] = t
            self.reverse[t] = original
        return self.forward[key]

    def operators(self, base: Policy | dict | None = None) -> dict:
        """Operator cho AnonymizerEngine.anonymize()."""
        base = base.operators if isinstance(base, Policy) else (base or get_policy().operators)
        ops = {}
        for ent, cfg in base.items():
            if cfg.operator_name == "replace":
                label = tag_label(cfg)
                ops[ent] = OperatorConfig("custom", {"lambda": lambda x, l=label: self.tag(l, x)})
            else:
                ops[ent] = cfg
        return ops

    @classmethod
    def from_mapping(cls, mapping: list[dict]) -> "Pseudonymizer":
        """Dựng lại từ mapping đã lưu (review: giá trị cũ giữ tag cũ, giá trị mới nhận số tiếp theo)."""
        p = cls()
        for m in mapping:
            label, n = m["tag"][1:-1].rsplit("_", 1)
            p.forward[(label, " ".join(m["original"].split()))] = m["tag"]
            p.reverse[m["tag"]] = m["original"]
            p.counter[label] = max(p.counter[label], int(n))
        return p

    def mapping_json(self):
        return [dict(tag=t, label=t[1:-1].rsplit("_", 1)[0], original=o) for t, o in self.reverse.items()]

    @staticmethod
    def restore_text(s: str, reverse: dict) -> str:
        return Pseudonymizer.TAG_RE.sub(lambda m: reverse.get(m.group(0), m.group(0)), s)
