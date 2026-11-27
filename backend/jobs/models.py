import uuid

from django.conf import settings
from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        PROCESSING = "processing"
        DONE = "done"                    # leak-check sạch
        NEEDS_REVIEW = "needs_review"    # còn nghi vấn / còn trang chưa xử lý (vd scan chưa OCR)
        FAILED = "failed"
        EXPIRED = "expired"              # file đã bị dọn theo TTL

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # PROTECT: xoá user phải xoá job trước qua services.delete_job (để xoá cả file trên storage)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="jobs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress = models.CharField(max_length=200, blank=True, default="")   # không chứa PII
    error = models.CharField(max_length=500, blank=True, default="")

    file_type = models.CharField(max_length=8)                 # .docx / .pdf / .json
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)                   # của file gốc: phát hiện upload trùng
    filename_enc = models.TextField()                          # tên file gốc (có thể chứa tên người) - mã hoá
    wrapped_key = models.TextField()                           # DEK của job, bọc bằng master key
    policy = models.CharField(max_length=32)
    use_ocr = models.BooleanField(default=True)

    stats = models.JSONField(default=dict, blank=True)         # số chỗ che theo loại, số segment...
    leak = models.JSONField(default=dict, blank=True)          # LeakReport.to_dict() - chỉ tag / vị trí
    warnings = models.JSONField(default=list, blank=True)
    timings = models.JSONField(default=dict, blank=True)

    original_key = models.CharField(max_length=300, blank=True, default="")
    work_key = models.CharField(max_length=300, blank=True, default="")
    output_key = models.CharField(max_length=300, blank=True, default="")
    mapping_key = models.CharField(max_length=300, blank=True, default="")
    original_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    output_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)   # lần review gần nhất
    review_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    @property
    def output_filename(self) -> str:
        # Không dùng tên file gốc (có thể chứa tên người)
        return f"masked_{str(self.id)[:8]}{self.file_type}"


class AuditLog(models.Model):
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    job_id = models.UUIDField(null=True, db_index=True)        # không FK: log còn sau khi job bị xoá
    action = models.CharField(max_length=32)                    # upload / processed / download / delete / expire
    actor = models.CharField(max_length=100, blank=True, default="")   # P5: user; P4: IP
    detail = models.JSONField(default=dict, blank=True)         # không chứa PII

    class Meta:
        ordering = ["-at"]
