"""Lưu object: ổ đĩa (dev) hoặc S3 / MinIO. Chỉ lưu byte, mã hoá do jobs.crypto lo.

Bố cục key (xem PLAN.md):
  originals/{job}/source.enc        file gốc (mã hoá)          - TTL ngắn
  intermediate/{job}/work.json.enc  segments, replacements, OCR (mã hoá) - TTL ngắn
  outputs/{job}/masked{ext}         file đã che                 - TTL dài
  mappings/{job}/mapping.json.enc   tag -> giá trị gốc (mã hoá), KHÔNG có API đọc
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django.conf import settings


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("key không hợp lệ")
        return p

    def put(self, key: str, data: bytes):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str):
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config
        self.bucket = settings.S3_BUCKET
        self.s3 = boto3.client(
            "s3", endpoint_url=settings.S3_ENDPOINT_URL, aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY, region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}))
        self._ensure_bucket()

    def _ensure_bucket(self):
        from botocore.exceptions import ClientError
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.s3.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str):
        self.s3.delete_object(Bucket=self.bucket, Key=key)


@lru_cache(maxsize=1)
def get_storage():
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage(settings.STORAGE_LOCAL_ROOT)
