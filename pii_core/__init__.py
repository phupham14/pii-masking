"""pii_core: nhận diện và che PII tiếng Việt (Presidio + luật VN). Không phụ thuộc Django.

    from pii_core import build_analyzer, Segment, mask_segments, apply_replacements
    analyze = build_analyzer("D")                       # load 1 lần / process
    res = mask_segments([Segment("s1", "Ông Nguyễn Văn An, CCCD 079185012345")], analyze, "bctc")
    apply_replacements(text, res.replacements)          # 'Ông <PERSON_1>, CCCD <ID_CARD_1>'
"""
from .engine import CONFIGS, ENTITIES, build_analyzer, build_config_A, build_config_D
from .policy import DEFAULT_POLICY, KEEP, POLICIES, Policy, get_policy
from .postprocess import OWNER_TYPED, classify_owner, drop_overlapping_address, resolve_conflicts, source
from .pseudonymizer import Pseudonymizer
from .segments import MaskResult, Replacement, Segment, apply_replacements, mask_segments, resolve_spans

__all__ = [
    "CONFIGS", "ENTITIES", "build_analyzer", "build_config_A", "build_config_D",
    "DEFAULT_POLICY", "KEEP", "POLICIES", "Policy", "get_policy",
    "OWNER_TYPED", "classify_owner", "drop_overlapping_address", "resolve_conflicts", "source",
    "Pseudonymizer",
    "MaskResult", "Replacement", "Segment", "apply_replacements", "mask_segments", "resolve_spans",
]
