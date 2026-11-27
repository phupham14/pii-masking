"""Mã hoá theo phong bì: mỗi job có khoá dữ liệu (DEK, Fernet) riêng, lưu trong DB dưới dạng đã bọc bằng master key.

- File gốc, segments, kết quả OCR, mapping, tên file gốc: mã hoá bằng DEK của job.
- Xoá job = xoá DEK đã bọc -> mọi bản sao còn sót (backup MinIO, file tạm) không giải mã được nữa.
- Đổi master key: PII_MASTER_KEYS="mới,cũ" (giải mã bằng key nào cũng được, mã hoá bằng key đầu)
  -> `manage.py rewrap_keys` bọc lại DEK bằng key mới -> bỏ key cũ. Dữ liệu (file trên MinIO) KHÔNG phải mã hoá lại.
"""
from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


@lru_cache(maxsize=1)
def _master() -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in settings.PII_MASTER_KEYS])


def rewrap(wrapped: str) -> str:
    """Bọc lại DEK bằng master key hiện hành (key đầu trong PII_MASTER_KEYS)."""
    return _master().rotate(wrapped.encode()).decode()


def new_wrapped_key() -> str:
    return _master().encrypt(Fernet.generate_key()).decode()


def _dek(wrapped: str) -> Fernet:
    return Fernet(_master().decrypt(wrapped.encode()))


def encrypt(wrapped: str, data: bytes) -> bytes:
    return _dek(wrapped).encrypt(data)


def decrypt(wrapped: str, token: bytes) -> bytes:
    return _dek(wrapped).decrypt(token)


def encrypt_text(wrapped: str, s: str) -> str:
    return encrypt(wrapped, s.encode("utf-8")).decode()


def decrypt_text(wrapped: str, s: str) -> str:
    return decrypt(wrapped, s.encode()).decode("utf-8")
