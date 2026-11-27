"""OCR cho trang PDF scan qua gateway LightOnOCR (tự host).

Cấu hình (biến môi trường): OCR_URL (mặc định https://asus.tail3f3df.ts.net), OCR_KEY (bắt buộc).
Tài liệu gửi đi CHƯA được che, nên mọi job luôn có:
  refine_tables_with_ai=false  - không cho gateway gửi bảng sang AI bên ngoài
  save_to_disk=false           - gateway không lưu file / kết quả xuống đĩa
Các giá trị này ghép SAU tham số của người gọi, nên người gọi không ghi đè được.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import requests

PRIVACY_FORM = {"refine_tables_with_ai": "false", "save_to_disk": "false"}
TERMINAL = {"completed", "failed", "cancelled"}


class OcrError(RuntimeError):
    pass


@dataclass
class OcrPage:
    page: int                     # số trang trong PDF gốc (1-based)
    markdown: str
    blocks: list[dict] = field(default_factory=list)   # mode grounding: vùng chữ + bbox (định dạng của gateway)
    raw: dict = field(default_factory=dict)


class OcrEngine(Protocol):
    def ocr_pdf(self, data: bytes, pages: list[int], progress: Callable[[str], None] | None = None) -> list[OcrPage]:
        ...


def _pages_param(pages: list[int]) -> str:
    return ",".join(str(p) for p in sorted(set(pages)))


class LightOnOcr:
    # mode: "transcription" (mặc định - chỉ cần markdown để dựng lại trang) hoặc "grounding" (thêm bbox cấp KHỐI:
    # đoạn / cả bảng / ảnh - không có cấp từ, nên chưa dùng để che trực tiếp trên ảnh scan)
    def __init__(self, url: str | None = None, key: str | None = None, mode: str = "transcription",
                 resolution: str = "2048", max_tokens: int = 4096, poll: float = 10.0, timeout: float = 1800.0):
        self.url = (url or os.environ.get("OCR_URL") or "https://asus.tail3f3df.ts.net").rstrip("/")
        self.key = key or os.environ.get("OCR_KEY")
        if not self.key:
            raise OcrError("thiếu OCR_KEY")
        self.mode, self.resolution, self.max_tokens = mode, resolution, max_tokens
        self.poll, self.timeout = poll, timeout
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = self.key

    def _call(self, method, path, retries=5, **kw):
        for attempt in range(retries):
            try:
                r = self.s.request(method, self.url + path, timeout=300, **kw)
            except requests.RequestException as e:
                err = e
            else:
                if r.status_code not in (429, 500, 502, 503, 504):
                    return r
                err = OcrError(f"HTTP {r.status_code}")
            time.sleep(10 * (attempt + 1))
        raise OcrError(f"gateway OCR không phản hồi ({method} {path}): {err}")

    def ocr_pdf(self, data: bytes, pages: list[int], progress=None) -> list[OcrPage]:
        if not pages:
            return []
        form = {"mode": self.mode, "pages": _pages_param(pages), "resolution": self.resolution,
                "max_tokens": str(self.max_tokens), **PRIVACY_FORM}
        r = self._call("POST", "/v1/ocr/async", files={"file": ("document.pdf", data, "application/pdf")}, data=form)
        if r.status_code not in (200, 202):
            raise OcrError(f"gửi job OCR lỗi HTTP {r.status_code}: {r.text[:200]}")
        job = r.json()["job_id"]
        t0, last = time.time(), None
        while True:
            st = self._call("GET", f"/v1/ocr/jobs/{job}", params={"include_result": "false"}).json()
            status = st.get("status")
            if status in TERMINAL:
                break
            msg = f"OCR {status} {(st.get('progress') or {}).get('percent') or ''}".strip()
            if progress and msg != last:
                progress(msg)
                last = msg
            if time.time() - t0 > self.timeout:
                self._call("POST", f"/v1/ocr/jobs/{job}/cancel")
                raise OcrError(f"OCR quá thời gian ({int(self.timeout)} s)")
            time.sleep(self.poll)
        if status != "completed":
            raise OcrError(f"OCR {status}: {st.get('error')}")
        res = self._call("GET", f"/v1/ocr/jobs/{job}/result", params={"format": "json"}).json()
        return parse_result(res, pages)


def parse_result(res: dict, pages: list[int]) -> list[OcrPage]:
    """Kết quả gateway -> OcrPage. Gateway đánh số trang theo PDF gốc khi gửi `pages`; nếu không khớp
    (đánh lại từ 1) thì ánh xạ theo thứ tự."""
    out = res.get("pages") or []
    wanted = sorted(set(pages))
    nums = [p.get("page") for p in out]
    remap = sorted(nums) != wanted and len(out) == len(wanted)
    return [OcrPage(page=wanted[i] if remap else p.get("page"), markdown=p.get("markdown") or "",
                    blocks=p.get("blocks") or [], raw=p) for i, p in enumerate(out)]


class StoredOcr:
    """OCR đã chạy trước đó ({số trang: markdown}, vd MaskedDocument.ocr_pages) - dùng cho render lại sau review."""

    def __init__(self, pages: dict[int, str]):
        self.pages = {int(k): v for k, v in pages.items()}

    def ocr_pdf(self, data: bytes, pages: list[int], progress=None) -> list[OcrPage]:
        missing = [p for p in pages if p not in self.pages]
        if missing:
            raise OcrError(f"không có kết quả OCR đã lưu cho trang {missing}")
        return [OcrPage(p, self.pages[p]) for p in pages]


class CachedOcr:
    """OCR từ file kết quả có sẵn (crawl/data/ocr/*.json) - để test / chạy lại không cần GPU."""

    def __init__(self, result_json: str | Path):
        self.res = json.loads(Path(result_json).read_text(encoding="utf-8"))

    def ocr_pdf(self, data: bytes, pages: list[int], progress=None) -> list[OcrPage]:
        """Trả các trang có trong cache; trang thiếu bị bỏ qua (PdfDocument để chúng ở `unprocessed`)."""
        got = {p.page: p for p in parse_result(self.res, [p.get("page") for p in self.res.get("pages", [])])}
        return [got[p] for p in pages if p in got]
