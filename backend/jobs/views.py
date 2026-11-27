from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from pii_core import DEFAULT_POLICY, POLICIES
from pii_core.documents import SUPPORTED
from pii_core.review import ReviewError

from . import services
from .models import Job
from .serializers import JobCreateSerializer, JobSerializer, ReviewSerializer
from .storage import get_storage

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
}


def exception_handler(exc, context):
    if isinstance(exc, (services.InvalidUpload, ReviewError)):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, services.ReviewUnavailable):
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    return drf_exception_handler(exc, context)


def _actor(request) -> str:
    return getattr(request.user, "username", "") or request.META.get("REMOTE_ADDR", "")


# ----------------------------------------------------------------------------- mô tả cho OpenAPI
_ERR = inline_serializer("Error", {"detail": serializers.CharField()})
_NOT_FOUND = OpenApiResponse(_ERR, description="Không có job này (hoặc job của người khác)")
_SPAN = inline_serializer("ReviewSpan", {
    "seg": serializers.CharField(), "start": serializers.IntegerField(), "end": serializers.IntegerField(),
    "entity": serializers.CharField(), "tag": serializers.CharField(), "score": serializers.FloatField(),
    "recognizer": serializers.CharField(help_text="tên recognizer / 'propagation' / 'manual'")})
_REVIEW_DATA = inline_serializer("ReviewData", {
    "segments": inline_serializer("ReviewSegment", {
        "id": serializers.CharField(), "text": serializers.CharField(help_text="TEXT GỐC - có PII"),
        "kind": serializers.CharField(), "page": serializers.IntegerField(allow_null=True)}, many=True),
    "replacements": _SPAN.__class__(many=True, help_text="chỗ đang che (offset theo text của segment)"),
    "detections": _SPAN.__class__(many=True, help_text="mọi chỗ phát hiện, kể cả loại được giữ (tag rỗng)"),
    "ignored": serializers.ListField(child=serializers.CharField(), help_text="giá trị đã bỏ che khi review"),
    "total_segments": serializers.IntegerField(),
    "maskable_entities": serializers.ListField(child=serializers.CharField(),
                                               help_text="loại entity được phép dùng trong `add`")})
_REVIEW_RESULT = inline_serializer("ReviewResult", {
    "removed": serializers.IntegerField(), "added": serializers.IntegerField(), "job": JobSerializer()})


def _get(request, job_id) -> Job:
    # job của người khác -> 404 (không 403): không cho dò id nào tồn tại
    try:
        return Job.objects.get(id=job_id, owner=request.user)
    except (Job.DoesNotExist, ValueError):
        raise NotFound("không có job này")


@extend_schema_view(post=extend_schema(tags=["Auth"], summary="Đăng nhập",
                                        description="Trả `access` (gửi kèm header `Authorization: Bearer <access>`, "
                                                    "hạn 30 phút) và `refresh` (hạn 12 giờ). Giới hạn 10 lần/phút/IP."))
class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


@extend_schema_view(post=extend_schema(tags=["Auth"], summary="Lấy access token mới từ refresh token"))
class RefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


@extend_schema(tags=["Auth"], summary="User đang đăng nhập",
               responses=inline_serializer("Me", {"username": serializers.CharField(),
                                                  "is_staff": serializers.BooleanField()}))
@api_view(["GET"])
def me(request):
    u = request.user
    return Response({"username": u.username, "is_staff": u.is_staff})


class UploadThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        if request.method != "POST":            # chỉ giới hạn upload, không giới hạn xem danh sách
            return True
        return super().allow_request(request, view)


@extend_schema(methods=["GET"], tags=["Jobs"], summary="Danh sách job của tôi (mới nhất trước)",
               parameters=[OpenApiParameter("limit", int, description="mặc định 50, tối đa 200")],
               responses=JobSerializer(many=True))
@extend_schema(methods=["POST"], tags=["Jobs"], summary="Upload file để che",
               description="multipart/form-data. Trả **202** ngay, xử lý chạy nền - poll `GET /api/jobs/{id}/`. "
                           "Định dạng: .docx, .pdf (scan: cần OCR bật ở server), .json. Giới hạn 60 lần/giờ.",
               request=JobCreateSerializer,
               responses={202: JobSerializer, 400: OpenApiResponse(_ERR, description="file / policy không hợp lệ"),
                          429: OpenApiResponse(_ERR, description="vượt giới hạn upload")})
@api_view(["GET", "POST"])
@throttle_classes([UploadThrottle])
def jobs(request):
    if request.method == "GET":
        qs = Job.objects.filter(owner=request.user)[: min(int(request.query_params.get("limit", 50)), 200)]
        return Response(JobSerializer(qs, many=True).data)
    s = JobCreateSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    f = s.validated_data["file"]
    job = services.create_job(f.name, f.read(), s.validated_data["policy"], s.validated_data["ocr"],
                              _actor(request), owner=request.user)
    services.enqueue(job)
    return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


jobs.cls.throttle_scope = "upload"


@extend_schema(methods=["GET"], tags=["Jobs"], summary="Trạng thái / kết quả một job",
               responses={200: JobSerializer, 404: _NOT_FOUND})
@extend_schema(methods=["DELETE"], tags=["Jobs"], summary="Xoá job và mọi file liên quan (ngay lập tức)",
               responses={204: None, 404: _NOT_FOUND,
                          409: OpenApiResponse(_ERR, description="job đang xử lý")})
@api_view(["GET", "DELETE"])
def job_detail(request, job_id):
    job = _get(request, job_id)
    if request.method == "DELETE":
        if job.status in (Job.Status.QUEUED, Job.Status.PROCESSING):
            return Response({"detail": "job đang xử lý, xoá sau khi xong"}, status=status.HTTP_409_CONFLICT)
        services.delete_job(job, _actor(request))
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(JobSerializer(job).data)


@extend_schema(tags=["Jobs"], summary="Tải file đã che",
               description="Dùng `download_url` trong job. Tên file: `masked_<8 ký tự id>.<đuôi>`. "
                           "Job `needs_review` phải kèm `confirm_review=1` (file có thể còn PII).",
               parameters=[OpenApiParameter("confirm_review", str, enum=["1"],
                                            description="bắt buộc khi job `needs_review`")],
               responses={(200, "application/octet-stream"): OpenApiTypes.BINARY, 404: _NOT_FOUND,
                          409: OpenApiResponse(_ERR, description="chưa có file / cần confirm_review=1")})
@api_view(["GET"])
def download(request, job_id):
    job = _get(request, job_id)
    if job.status not in (Job.Status.DONE, Job.Status.NEEDS_REVIEW) or not job.output_key:
        return Response({"detail": f"chưa có file (trạng thái: {job.status})"}, status=status.HTTP_409_CONFLICT)
    if job.status == Job.Status.NEEDS_REVIEW and request.query_params.get("confirm_review") != "1":
        # file có thể còn PII (trang chưa xử lý / nghi vấn): người tải phải xác nhận đã biết
        return Response({"detail": "file cần review: có thể còn thông tin chưa được che. "
                                   "Thêm ?confirm_review=1 để tải.", "leak": job.leak, "warnings": job.warnings},
                        status=status.HTTP_409_CONFLICT)
    data = get_storage().get(job.output_key)
    services.audit("download", job.id, _actor(request), status=job.status)
    resp = HttpResponse(data, content_type=CONTENT_TYPES[job.file_type])
    resp["Content-Disposition"] = f'attachment; filename="{job.output_filename}"'
    resp["X-Job-Status"] = job.status
    resp["Cache-Control"] = "no-store"
    return resp


class ReviewThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        return request.method != "POST" or super().allow_request(request, view)


@extend_schema(methods=["GET"], tags=["Review"], summary="Dữ liệu review (CHỨA TEXT GỐC)",
               description="Mặc định chỉ trả segment có chỗ che / phát hiện. Mọi lần xem đều ghi audit.",
               parameters=[OpenApiParameter("all", str, enum=["1"], description="trả mọi segment")],
               responses={200: _REVIEW_DATA, 404: _NOT_FOUND,
                          409: OpenApiResponse(_ERR, description="job chưa xong / file gốc đã hết hạn")})
@extend_schema(methods=["POST"], tags=["Review"], summary="Sửa chỗ che rồi xuất lại file",
               description="`remove`: bỏ chỗ che nhầm (theo đúng seg/start/end của replacement). "
                           "`add`: che thêm đoạn bị sót, `entity` thuộc `maskable_entities`. "
                           "`all: true` áp cho mọi chỗ cùng giá trị. Trả **202**, xuất lại chạy nền.",
               request=ReviewSerializer,
               responses={202: _REVIEW_RESULT, 400: OpenApiResponse(_ERR, description="chỉnh sửa không hợp lệ"),
                          404: _NOT_FOUND, 409: OpenApiResponse(_ERR, description="job không ở trạng thái review được")})
@api_view(["GET", "POST"])
@throttle_classes([ReviewThrottle])
def review(request, job_id):
    """GET: các segment có chỗ che / phát hiện (?all=1: mọi segment) - CHỨA TEXT GỐC.
    POST: {"remove": [...], "add": [...]} -> lưu, xuất lại file (202)."""
    job = _get(request, job_id)
    if request.method == "GET":
        data = services.get_review(job, request.query_params.get("all") == "1", _actor(request))
        resp = Response(data)
        resp["Cache-Control"] = "no-store"
        return resp
    s = ReviewSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    counts = services.submit_review(job, s.validated_data, _actor(request))
    job.refresh_from_db()
    return Response(dict(counts, job=JobSerializer(job).data), status=status.HTTP_202_ACCEPTED)


review.cls.throttle_scope = "review"


@extend_schema(tags=["Meta"], summary="Các policy che và định dạng hỗ trợ",
               responses=inline_serializer("Policies", {
                   "default": serializers.CharField(),
                   "formats": serializers.ListField(child=serializers.CharField()),
                   "policies": inline_serializer("Policy", {"name": serializers.CharField(),
                                                            "label": serializers.CharField(),
                                                            "description": serializers.CharField()}, many=True)}))
@api_view(["GET"])
def policies(request):
    return Response({"default": DEFAULT_POLICY, "formats": sorted(SUPPORTED),
                     "policies": [dict(name=p.name, label=p.label, description=p.description)
                                  for p in POLICIES.values()]})


@extend_schema(tags=["Meta"], summary="Kiểm tra sống (không cần đăng nhập)", auth=[],
               responses=inline_serializer("Health", {"ok": serializers.BooleanField()}))
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"ok": True})
