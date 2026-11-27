from rest_framework import serializers

from .models import Job


class JobSerializer(serializers.ModelSerializer):
    """Không trả tên file gốc, khoá, object key hay mapping."""
    download_url = serializers.SerializerMethodField()
    output_filename = serializers.CharField(read_only=True)

    class Meta:
        model = Job
        fields = ["id", "status", "progress", "error", "file_type", "size_bytes", "policy", "use_ocr", "stats",
                  "leak", "warnings", "timings", "created_at", "finished_at", "original_expires_at",
                  "output_expires_at", "output_filename", "download_url"]

    def get_download_url(self, job) -> str | None:
        """null khi chưa có file; job needs_review: URL đã kèm ?confirm_review=1"""
        if job.status not in (Job.Status.DONE, Job.Status.NEEDS_REVIEW) or not job.output_key:
            return None
        url = f"/api/jobs/{job.id}/download/"
        return url if job.status == Job.Status.DONE else url + "?confirm_review=1"


class _Span(serializers.Serializer):
    seg = serializers.CharField(max_length=300)
    start = serializers.IntegerField(min_value=0)
    end = serializers.IntegerField(min_value=1)
    all = serializers.BooleanField(required=False, default=False)   # áp cho mọi chỗ cùng giá trị


class _AddSpan(_Span):
    entity = serializers.CharField(max_length=64)


class ReviewSerializer(serializers.Serializer):
    remove = _Span(many=True, required=False, default=list)
    add = _AddSpan(many=True, required=False, default=list)

    def validate(self, data):
        if not data["remove"] and not data["add"]:
            raise serializers.ValidationError("không có thay đổi nào")
        if len(data["remove"]) + len(data["add"]) > 2000:
            raise serializers.ValidationError("quá nhiều thay đổi trong một lần (tối đa 2000)")
        return data


class JobCreateSerializer(serializers.Serializer):
    file = serializers.FileField()
    policy = serializers.CharField(required=False, default="bctc")
    ocr = serializers.BooleanField(required=False, default=True)
