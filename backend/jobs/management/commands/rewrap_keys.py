"""Đổi master key: bọc lại khoá của mọi job bằng key đầu tiên trong PII_MASTER_KEYS.

    1. PII_MASTER_KEYS="key_mới,key_cũ"  (khởi động lại api / worker)
    2. python manage.py rewrap_keys
    3. PII_MASTER_KEYS="key_mới"          (khởi động lại) -> key cũ hết tác dụng
File trên storage KHÔNG phải mã hoá lại (chỉ khoá của job, nằm trong DB, đổi lớp bọc).
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from jobs import crypto
from jobs.models import Job


class Command(BaseCommand):
    help = "Bọc lại khoá của các job bằng master key hiện hành"

    def handle(self, **_):
        n = 0
        for job in Job.objects.exclude(wrapped_key="").only("id", "wrapped_key").iterator():
            with transaction.atomic():
                new = crypto.rewrap(job.wrapped_key)
                Job.objects.filter(id=job.id).update(wrapped_key=new)
            n += 1
        self.stdout.write(f"đã bọc lại {n} khoá")
