import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path.insert(0, str(APP))


@pytest.fixture(scope="session")
def analyze():
    from pii_core import build_analyzer
    return build_analyzer("D")


@pytest.fixture(scope="session")
def eval_texts():
    """Văn bản của bộ eval tự xây (dữ liệu hư cấu). Bỏ qua nếu repo không có eval/data."""
    import json
    texts = []
    for name, limit in (("l4", None), ("l2_test", 300)):
        p = ROOT / "eval" / "data" / f"{name}.jsonl"
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        texts += [json.loads(line)["text"] for line in lines[:limit] if line.strip()]
    if not texts:
        pytest.skip("không có eval/data")
    return texts
