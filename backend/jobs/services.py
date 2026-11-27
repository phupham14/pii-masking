"""Nghiệp vụ job: tạo, xử lý (gọi pii_core), xoá, dọn theo TTL. Task Celery chỉ gọi lại các hàm này."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import timedelta
from functools import lru_cache

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pii_core import POLICIES, Replacement, Segment, build_analyzer, get_policy
from pii_core.documents import SUPPORTED, mask_document, rerender
from pii_core.ocr import StoredOcr
from pii_core.review import ReviewError, apply_edits

from . import crypto
from .models import AuditLog, Job
from .storage import get_storage

log = logging.getLogger(__name__)

# Byte đầu file theo định dạng: chặn file đổi đuôi
MAGIC = {".pdf": (b"%PDF",), ".docx": (b"PK\x03\x04",), ".json": (b"{", b"\xef\xbb\xbf{", b"[")}


class InvalidUpload(ValueError):
    pass


@lru_cache(maxsize=1)
def analyzer():
    """Dựng 1 lần / process (load pyvi, spaCy blank ~0,5 s)."""
    return build_analyzer(settings.PII_ANALYZER_CONFIG)


def ocr_engine():
    if not settings.OCR_ENABLED:
        return None
    from pii_core.ocr import LightOnOcr
    return LightOnOcr()


def audit(action: str, job_id=None, actor: str = "", **detail):
    AuditLog.objects.create(action=action, job_id=job_id, actor=actor[:100], detail=detail)


# ----------------------------------------------------------------------------- tạo
def validate_upload(name: str, data: bytes) -> str:
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in SUPPORTED:
        raise InvalidUpload(f"định dạng '{ext or '?'}' chưa hỗ trợ (hỗ trợ: {', '.join(sorted(SUPPORTED))})")
    if not data:
        raise InvalidUpload("file rỗng")
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise InvalidUpload(f"file quá {settings.MAX_UPLOAD_MB} MB")
    head = data[:8].lstrip() if ext == ".json" else data[:8]
    if not head.startswith(MAGIC[ext]):
        raise InvalidUpload(f"nội dung không phải file {ext}")
    return ext


def create_job(name: str, data: bytes, policy: str, use_ocr: bool = True, actor: str = "", owner=None) -> Job:
    ext = validate_upload(name, data)
    if policy not in POLICIES:
        raise InvalidUpload(f"policy '{policy}' không tồn tại (có: {', '.join(sorted(POLICIES))})")
    key = crypto.new_wrapped_key()
    job = Job(owner=owner, file_type=ext, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
              filename_enc=crypto.encrypt_text(key, name[:255]), wrapped_key=key, policy=policy,
              use_ocr=use_ocr, original_expires_at=timezone.now() + timedelta(hours=settings.ORIGINAL_TTL_HOURS))
    job.original_key = f"originals/{job.id}/source.enc"
    get_storage().put(job.original_key, crypto.encrypt(key, data))
    job.save()
    audit("upload", job.id, actor, file_type=ext, size=len(data), policy=policy)
    return job


def enqueue(job: Job):
    from .tasks import process_job_task
    # chỉ đẩy task khi transaction đã commit (worker phải thấy được job)
    transaction.on_commit(lambda: process_job_task.delay(str(job.id)))


# ----------------------------------------------------------------------------- xử lý
def _set(job: Job, **fields):
    for k, v in fields.items():
        setattr(job, k, v)
    job.save(update_fields=[*fields, "updated_at"])


def process_job(job_id: str):
    job = Job.objects.get(id=job_id)
    # chỉ job chưa từng xong: task bị giao lại (acks_late) không được che lại từ đầu, ghi đè kết quả review
    if job.status not in (Job.Status.QUEUED, Job.Status.PROCESSING) or job.finished_at is not None:
        return
    _set(job, status=Job.Status.PROCESSING, progress="đang xử lý", error="")
    storage, t0 = get_storage(), time.perf_counter()
    last = {"t": 0.0}

    def progress(msg: str):
        if time.perf_counter() - last["t"] > 5:          # không ghi DB quá dày khi poll OCR
            last["t"] = time.perf_counter()
            _set(job, progress=msg[:200])

    try:
        data = crypto.decrypt(job.wrapped_key, storage.get(job.original_key))
        ocr = ocr_engine() if job.use_ocr and job.file_type == ".pdf" else None
        r = mask_document(data, f"x{job.file_type}", analyzer(), job.policy, ocr=ocr, progress=progress)
    except Exception as e:  # noqa: BLE001 - mọi lỗi đều phải ghi vào job, không để job treo
        log.exception("job %s lỗi", job.id)
        # thông điệp lỗi của pii_core không chứa nội dung tài liệu; vẫn cắt ngắn
        _set(job, status=Job.Status.FAILED, error=f"{type(e).__name__}: {str(e)[:400]}", progress="",
             finished_at=timezone.now())
        audit("failed", job.id, error=type(e).__name__)
        return

    t1 = time.perf_counter()
    by_entity = _count(r.result.replacements)
    work = dict(segments=[s.__dict__ for s in r.segments], **r.result.to_dict(),
                ocr_pages={str(k): v for k, v in r.ocr_pages.items()}, ignored=[])
    job.output_key = f"outputs/{job.id}/masked{job.file_type}"
    job.mapping_key = f"mappings/{job.id}/mapping.json.enc"
    job.work_key = f"intermediate/{job.id}/work.json.enc"
    storage.put(job.output_key, r.data)
    storage.put(job.mapping_key, crypto.encrypt(job.wrapped_key, json.dumps(r.result.mapping, ensure_ascii=False)
                                                .encode("utf-8")))
    storage.put(job.work_key, crypto.encrypt(job.wrapped_key, json.dumps(work, ensure_ascii=False).encode("utf-8")))
    _set(job, status=Job.Status.DONE if r.status == "done" else Job.Status.NEEDS_REVIEW, progress="",
         stats=dict(segments=len(r.segments), replacements=len(r.result.replacements),
                    distinct_values=len(r.result.mapping), by_entity=by_entity, ocr_pages=len(r.ocr_pages)),
         leak=r.leak.to_dict(), warnings=r.leak.warnings,
         timings=dict(mask_s=round(t1 - t0, 2), total_s=round(time.perf_counter() - t0, 2)),
         output_key=job.output_key, mapping_key=job.mapping_key, work_key=job.work_key,
         output_expires_at=timezone.now() + timedelta(days=settings.OUTPUT_TTL_DAYS), finished_at=timezone.now())
    audit("processed", job.id, status=job.status, replacements=len(r.result.replacements))


def _count(reps) -> dict:
    out = {}
    for rep in reps:
        out[rep.entity] = out.get(rep.entity, 0) + 1
    return out


# ----------------------------------------------------------------------------- review
class ReviewUnavailable(ValueError):
    pass


def _load_json(job: Job, key: str):
    return json.loads(crypto.decrypt(job.wrapped_key, get_storage().get(key)))


def _put_json(job: Job, key: str, obj):
    get_storage().put(key, crypto.encrypt(job.wrapped_key, json.dumps(obj, ensure_ascii=False).encode("utf-8")))


def _check_reviewable(job: Job):
    if job.status not in (Job.Status.DONE, Job.Status.NEEDS_REVIEW):
        raise ReviewUnavailable(f"không review được job ở trạng thái '{job.status}'")
    if not job.work_key or not job.original_key:
        raise ReviewUnavailable("dữ liệu để review đã bị xoá theo hạn lưu file gốc")


def get_review(job: Job, all_segments: bool = False, actor: str = "") -> dict:
    """Dữ liệu cho màn review. CHỨA TEXT GỐC (có PII) -> chỉ chủ job, có audit."""
    _check_reviewable(job)
    work = _load_json(job, job.work_key)
    marked = {r["seg"] for r in work["replacements"]} | {d["seg"] for d in work["detections"]}
    segs = [dict(id=s["id"], text=s["text"], kind=s["meta"].get("kind"), page=s["meta"].get("page"))
            for s in work["segments"] if all_segments or s["id"] in marked]
    policy = get_policy(job.policy)
    audit("review_view", job.id, actor, segments=len(segs))
    return dict(segments=segs, replacements=work["replacements"], detections=work["detections"],
                ignored=work.get("ignored", []), total_segments=len(work["segments"]),
                maskable_entities=sorted(e for e in policy.operators if e != "DEFAULT" and policy.is_masked(e)))


def submit_review(job: Job, edits: dict, actor: str = "") -> dict:
    """Áp chỉnh sửa, lưu lại, đưa job vào hàng đợi xuất lại file. Trả số chỗ bỏ / thêm."""
    with transaction.atomic():
        job = Job.objects.select_for_update().get(id=job.id)      # chặn 2 lần gửi review cùng lúc
        _check_reviewable(job)
        work = _load_json(job, job.work_key)
        mapping = _load_json(job, job.mapping_key)
        segments = [Segment(s["id"], s["text"], s["meta"]) for s in work["segments"]]
        reps = [Replacement(**r) for r in work["replacements"]]
        res = apply_edits(segments, reps, mapping, job.policy, edits, work.get("ignored", []))   # ReviewError -> 400
        work["replacements"] = [r.__dict__ for r in res.replacements]
        work["ignored"] = res.ignored
        _put_json(job, job.work_key, work)
        _put_json(job, job.mapping_key, res.mapping)
        _set(job, status=Job.Status.QUEUED, progress="đang xuất lại sau review", error="")
        from .tasks import render_job_task
        transaction.on_commit(lambda: render_job_task.delay(str(job.id)))
    audit("review_submit", job.id, actor, removed=res.removed, added=res.added)
    return dict(removed=res.removed, added=res.added)


def render_job(job_id: str):
    """Xuất lại file từ replacements đã review (OCR lấy từ work.json, không gọi lại gateway)."""
    job = Job.objects.get(id=job_id)
    if job.status not in (Job.Status.QUEUED, Job.Status.PROCESSING):
        return
    _set(job, status=Job.Status.PROCESSING, progress="đang xuất lại")
    t0 = time.perf_counter()
    try:
        storage = get_storage()
        data = crypto.decrypt(job.wrapped_key, storage.get(job.original_key))
        work, mapping = _load_json(job, job.work_key), _load_json(job, job.mapping_key)
        reps = [Replacement(**r) for r in work["replacements"]]
        ocr = StoredOcr({int(k): v for k, v in work.get("ocr_pages", {}).items()}) if work.get("ocr_pages") else None
        out, leak = rerender(data, f"x{job.file_type}", reps, mapping, analyzer(), job.policy, ocr=ocr,
                             ignore=work.get("ignored", []))
    except Exception as e:  # noqa: BLE001
        log.exception("render job %s lỗi", job.id)
        _set(job, status=Job.Status.FAILED, error=f"{type(e).__name__}: {str(e)[:400]}", progress="")
        audit("failed", job.id, error=type(e).__name__, stage="render")
        return
    storage.put(job.output_key, out)
    stats = dict(job.stats, replacements=len(reps), distinct_values=len(mapping), by_entity=_count(reps),
                 ignored=len(work.get("ignored", [])))
    _set(job, status=Job.Status.DONE if leak.clean else Job.Status.NEEDS_REVIEW, progress="", stats=stats,
         leak=leak.to_dict(), warnings=leak.warnings, reviewed_at=timezone.now(), review_count=job.review_count + 1,
         timings=dict(job.timings, render_s=round(time.perf_counter() - t0, 2)), finished_at=timezone.now())
    audit("rerendered", job.id, status=job.status, replacements=len(reps))


# ----------------------------------------------------------------------------- xoá / dọn
def _delete_keys(job: Job, *fields):
    storage = get_storage()
    for f in fields:
        key = getattr(job, f)
        if key:
            storage.delete(key)
            setattr(job, f, "")


def delete_job(job: Job, actor: str = ""):
    _delete_keys(job, "original_key", "work_key", "output_key", "mapping_key")
    job_id = job.id
    job.delete()                                         # xoá luôn DEK -> bản sao còn sót không giải mã được
    audit("delete", job_id, actor)


def cleanup_expired(now=None) -> dict:
    now = now or timezone.now()
    n_orig = n_out = 0
    for job in Job.objects.filter(original_expires_at__lte=now).exclude(original_key="", work_key=""):
        if job.status in (Job.Status.QUEUED, Job.Status.PROCESSING):
            continue
        _delete_keys(job, "original_key", "work_key")
        job.save(update_fields=["original_key", "work_key", "updated_at"])
        n_orig += 1
    for job in Job.objects.filter(output_expires_at__lte=now).exclude(status=Job.Status.EXPIRED):
        _delete_keys(job, "original_key", "work_key", "output_key", "mapping_key")
        job.status, job.wrapped_key = Job.Status.EXPIRED, ""
        job.save(update_fields=["original_key", "work_key", "output_key", "mapping_key", "status", "wrapped_key",
                                "updated_at"])
        audit("expire", job.id)
        n_out += 1
    return dict(originals=n_orig, outputs=n_out)
