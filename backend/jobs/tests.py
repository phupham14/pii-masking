"""Test backend: python manage.py test jobs. Celery chạy đồng bộ, storage = thư mục tạm. Dữ liệu hư cấu."""
import io
import json
import shutil
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

from unittest import mock

import docx
import pymupdf
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from . import crypto, services
from .models import AuditLog, Job
from .storage import get_storage

FILENAME = "Hop dong Nguyen Van An.docx"      # tên file chứa tên người -> không được lộ ra API
User = get_user_model()


def make_docx() -> bytes:
    d = docx.Document()
    d.add_paragraph("Hợp đồng với Ông Nguyễn Văn An, CCCD số 079185012345, điện thoại 0912 345 678.")
    d.add_paragraph("Công ty có trụ sở tại số 111A, Đường Pasteur, Phường Bến Nghé, Quận 1.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_scan_pdf() -> bytes:
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.set_rect(pix.irect, (230, 230, 230))
    p = doc.new_page()
    p.insert_image(p.rect, pixmap=pix)
    return doc.tobytes()


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode("utf-8")


class Base(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(STORAGE_BACKEND="local", STORAGE_LOCAL_ROOT=Path(self.tmp),
                                          CELERY_TASK_ALWAYS_EAGER=True, OCR_ENABLED=False)
        self.override.enable()
        get_storage.cache_clear()
        cache.clear()                                   # bộ đếm throttle
        self.user = User.objects.create_user("alice", password="mat-khau-dai-cua-alice")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def tearDown(self):
        self.override.disable()
        get_storage.cache_clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, data, name=FILENAME, **extra):
        f = io.BytesIO(data)
        f.name = name
        with self.captureOnCommitCallbacks(execute=True):
            return self.api.post("/api/jobs/", {"file": f, **extra}, format="multipart")


class JobFlowTests(Base):
    def test_upload_process_download(self):
        r = self.upload(make_docx())
        self.assertEqual(r.status_code, 202, r.content)
        job_id = r.json()["id"]
        detail = self.api.get(f"/api/jobs/{job_id}/").json()
        self.assertEqual(detail["status"], "done", detail)
        self.assertEqual(detail["stats"]["by_entity"].get("PERSON"), 1)
        self.assertTrue(detail["leak"]["clean"])

        dl = self.api.get(detail["download_url"])
        self.assertEqual(dl.status_code, 200)
        self.assertIn(f'filename="masked_{job_id[:8]}.docx"', dl["Content-Disposition"])
        text = docx_text(dl.content)
        for v in ("Nguyễn Văn An", "079185012345", "0912 345 678"):
            self.assertNotIn(v, text)
        self.assertIn("Đường Pasteur", text)
        self.assertTrue(AuditLog.objects.filter(job_id=job_id, action="download").exists())

    def test_filename_never_exposed(self):
        r = self.upload(make_docx())
        job_id = r.json()["id"]
        for body in (r.content, self.api.get(f"/api/jobs/{job_id}/").content, self.api.get("/api/jobs/").content):
            self.assertNotIn(b"Nguyen Van An", body)
        job = Job.objects.get(id=job_id)
        self.assertNotIn("Nguyen", job.filename_enc)
        self.assertEqual(crypto.decrypt_text(job.wrapped_key, job.filename_enc), FILENAME)

    def test_stored_objects_encrypted(self):
        job = Job.objects.get(id=self.upload(make_docx()).json()["id"])
        st = get_storage()
        orig, mapping, work = st.get(job.original_key), st.get(job.mapping_key), st.get(job.work_key)
        for blob in (orig, mapping, work):
            self.assertFalse(blob.startswith(b"PK"))
            self.assertNotIn("Nguyễn".encode(), blob)
        m = json.loads(crypto.decrypt(job.wrapped_key, mapping))
        self.assertIn("Nguyễn Văn An", {x["original"] for x in m})
        self.assertTrue(st.get(job.output_key).startswith(b"PK"))     # file đã che: không mã hoá

    def test_needs_review_requires_confirmation(self):
        r = self.upload(make_scan_pdf(), name="scan.pdf")
        job = self.api.get(f"/api/jobs/{r.json()['id']}/").json()
        self.assertEqual(job["status"], "needs_review")
        self.assertEqual(job["leak"]["unprocessed"], [{"page": 1, "kind": "scan"}])
        self.assertEqual(self.api.get(f"/api/jobs/{job['id']}/download/").status_code, 409)
        self.assertEqual(self.api.get(job["download_url"]).status_code, 200)   # url đã kèm confirm_review=1

    def test_delete_removes_files_keeps_audit(self):
        job = Job.objects.get(id=self.upload(make_docx()).json()["id"])
        keys = [job.original_key, job.work_key, job.output_key, job.mapping_key]
        self.assertEqual(self.api.delete(f"/api/jobs/{job.id}/").status_code, 204)
        self.assertFalse(Job.objects.filter(id=job.id).exists())
        for k in keys:
            self.assertFalse(get_storage().exists(k), k)
        self.assertTrue(AuditLog.objects.filter(job_id=job.id, action="delete").exists())
        self.assertEqual(self.api.get(f"/api/jobs/{job.id}/").status_code, 404)

    def test_cleanup_expired(self):
        job = Job.objects.get(id=self.upload(make_docx()).json()["id"])
        st = get_storage()
        res = services.cleanup_expired(now=timezone.now() + timedelta(hours=25))
        job.refresh_from_db()
        self.assertEqual(res["originals"], 1)
        self.assertEqual(job.original_key, "")
        self.assertTrue(st.exists(job.output_key))                    # file đã che còn
        services.cleanup_expired(now=timezone.now() + timedelta(days=31))
        job.refresh_from_db()
        self.assertEqual(job.status, "expired")
        self.assertEqual((job.output_key, job.mapping_key, job.wrapped_key), ("", "", ""))

    def test_corrupt_file_fails_cleanly(self):
        r = self.upload(b"PK\x03\x04 not really a docx", name="x.docx")
        job = self.api.get(f"/api/jobs/{r.json()['id']}/").json()
        self.assertEqual(job["status"], "failed")
        self.assertIn("DocxError", job["error"])
        self.assertIsNone(job["download_url"])


class ValidationTests(Base):
    def test_rejects(self):
        cases = [
            (b"hello", "a.txt", {}, "chưa hỗ trợ"),
            (b"MZ\x90\x00 exe", "a.pdf", {}, "không phải file .pdf"),
            (make_docx(), "a.docx", {"policy": "khong-co"}, "policy"),
            (b"", "a.docx", {}, "empty"),          # DRF chặn trước (FileField)
        ]
        for data, name, extra, msg in cases:
            r = self.upload(data, name=name, **extra)
            self.assertEqual(r.status_code, 400, (name, r.content))
            self.assertIn(msg, json.dumps(r.json(), ensure_ascii=False))
        self.assertEqual(Job.objects.count(), 0)

    @override_settings(MAX_UPLOAD_MB=0)
    def test_too_big(self):
        self.assertEqual(self.upload(make_docx()).status_code, 400)

    def test_policies_endpoint(self):
        r = self.api.get("/api/policies/").json()
        self.assertEqual(r["default"], "bctc")
        self.assertEqual({p["name"] for p in r["policies"]}, {"bctc", "personal"})
        self.assertEqual(r["formats"], [".docx", ".json", ".pdf"])


class AuthTests(Base):
    def test_requires_login(self):
        anon = APIClient()
        self.assertEqual(anon.get("/api/jobs/").status_code, 401)
        self.assertEqual(anon.get("/api/policies/").status_code, 401)
        self.assertEqual(anon.get("/api/health/").status_code, 200)

    def test_api_docs_public(self):
        anon = APIClient()
        schema = anon.get("/api/schema/?format=json")
        self.assertEqual(schema.status_code, 200)
        paths = json.loads(schema.content)["paths"]
        for p in ("/api/jobs/", "/api/jobs/{job_id}/review/", "/api/auth/token/"):
            self.assertIn(p, paths)
        self.assertEqual(json.loads(schema.content)["components"]["securitySchemes"]["jwtAuth"]["scheme"], "bearer")
        self.assertEqual(anon.get("/api/docs/").status_code, 200)

    def test_jwt_login_flow(self):
        anon = APIClient()
        bad = anon.post("/api/auth/token/", {"username": "alice", "password": "sai"}, format="json")
        self.assertEqual(bad.status_code, 401)
        tok = anon.post("/api/auth/token/", {"username": "alice", "password": "mat-khau-dai-cua-alice"},
                        format="json").json()
        anon.credentials(HTTP_AUTHORIZATION=f"Bearer {tok['access']}")
        self.assertEqual(anon.get("/api/auth/me/").json()["username"], "alice")
        new = anon.post("/api/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json").json()
        self.assertIn("access", new)

    def test_other_users_jobs_invisible(self):
        job_id = self.upload(make_docx()).json()["id"]
        bob = APIClient()
        bob.force_authenticate(User.objects.create_user("bob", password="mat-khau-dai-cua-bob"))
        self.assertEqual(bob.get("/api/jobs/").json(), [])
        for method, url in (("get", f"/api/jobs/{job_id}/"), ("get", f"/api/jobs/{job_id}/download/"),
                            ("get", f"/api/jobs/{job_id}/review/"), ("delete", f"/api/jobs/{job_id}/")):
            self.assertEqual(getattr(bob, method)(url).status_code, 404, url)   # 404, không phải 403
        self.assertTrue(Job.objects.filter(id=job_id).exists())
        self.assertEqual(AuditLog.objects.get(job_id=job_id, action="upload").actor, "alice")

    def test_login_throttled(self):
        anon = APIClient()
        with mock.patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"login": "3/minute"}):
            codes = [anon.post("/api/auth/token/", {"username": "alice", "password": "sai"}, format="json")
                     .status_code for _ in range(5)]
        self.assertEqual(codes, [401, 401, 401, 429, 429])

    def test_upload_throttled_but_listing_not(self):
        with mock.patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"upload": "2/hour"}):
            codes = [self.upload(make_docx()).status_code for _ in range(3)]
            self.assertEqual(codes, [202, 202, 429])
            self.assertEqual(self.api.get("/api/jobs/").status_code, 200)


class ReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.job_id = self.upload(make_docx()).json()["id"]
        self.url = f"/api/jobs/{self.job_id}/review/"

    def post(self, body):
        with self.captureOnCommitCallbacks(execute=True):
            return self.api.post(self.url, body, format="json")

    def output_text(self):
        job = self.api.get(f"/api/jobs/{self.job_id}/").json()
        return job, docx_text(self.api.get(job["download_url"]).content)

    def test_get_review(self):
        r = self.api.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "no-store")
        data = r.json()
        self.assertIn("Nguyễn Văn An", data["segments"][0]["text"])
        self.assertEqual({x["entity"] for x in data["replacements"]}, {"PERSON", "VN_ID_CARD", "VN_PHONE_NUMBER"})
        self.assertIn("PERSON", data["maskable_entities"])
        self.assertNotIn("LOCATION", data["maskable_entities"])
        self.assertTrue(AuditLog.objects.filter(job_id=self.job_id, action="review_view").exists())

    def test_remove_false_positive_then_stays_clean(self):
        rep = next(x for x in self.api.get(self.url).json()["replacements"] if x["entity"] == "VN_PHONE_NUMBER")
        r = self.post({"remove": [{"seg": rep["seg"], "start": rep["start"], "end": rep["end"]}]})
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json()["removed"], 1)
        job, text = self.output_text()
        self.assertIn("0912 345 678", text)                       # người review quyết định giữ
        self.assertNotIn("Nguyễn Văn An", text)
        self.assertEqual(job["status"], "done", job["leak"])      # detector tìm lại SĐT nhưng đã bị bỏ qua
        self.assertEqual(job["stats"]["ignored"], 1)
        self.assertEqual(self.api.get(self.url).json()["ignored"], ["0912 345 678"])

    def test_add_missed_value_all_occurrences(self):
        data = self.api.get(self.url + "?all=1").json()
        seg = next(s for s in data["segments"] if "Bến Nghé" in s["text"])
        i = seg["text"].index("Bến Nghé")
        r = self.post({"add": [{"seg": seg["id"], "start": i, "end": i + len("Bến Nghé"),
                                "entity": "VN_ADDRESS_PERSONAL", "all": True}]})
        self.assertEqual(r.status_code, 202, r.content)
        job, text = self.output_text()
        self.assertNotIn("Bến Nghé", text)
        self.assertIn("Phường &lt;ADDRESS_1&gt;, Quận 1", text)     # XML thô: "<" được escape
        self.assertEqual(Job.objects.get(id=self.job_id).review_count, 1)
        mapping = json.loads(crypto.decrypt(Job.objects.get(id=self.job_id).wrapped_key,
                                            get_storage().get(Job.objects.get(id=self.job_id).mapping_key)))
        self.assertIn({"tag": "<ADDRESS_1>", "label": "ADDRESS", "original": "Bến Nghé"}, mapping)

    def test_invalid_edits(self):
        data = self.api.get(self.url + "?all=1").json()
        rep = data["replacements"][0]
        seg = next(s for s in data["segments"] if "Bến Nghé" in s["text"])
        i = seg["text"].index("Bến Nghé")
        cases = [
            {"add": [{"seg": seg["id"], "start": i, "end": i + 8, "entity": "LOCATION"}]},          # loại "giữ"
            {"add": [{"seg": rep["seg"], "start": rep["start"], "end": rep["end"], "entity": "PERSON"}]},  # chồng
            {"add": [{"seg": seg["id"], "start": 0, "end": 99999, "entity": "PERSON"}]},            # ngoài segment
            {"remove": [{"seg": seg["id"], "start": 0, "end": 3}]},                                  # không có
            {},                                                                                      # rỗng
        ]
        for body in cases:
            self.assertEqual(self.post(body).status_code, 400, body)
        self.assertEqual(Job.objects.get(id=self.job_id).status, "done")

    def test_review_after_original_expired(self):
        services.cleanup_expired(now=timezone.now() + timedelta(hours=25))
        self.assertEqual(self.api.get(self.url).status_code, 409)


class AdminCommandTests(Base):
    def test_rewrap_master_key(self):
        job = Job.objects.get(id=self.upload(make_docx()).json()["id"])
        old_key, new_key = settings_keys(), Fernet.generate_key().decode()
        with override_settings(PII_MASTER_KEYS=[new_key, *old_key]):
            crypto._master.cache_clear()
            call_command("rewrap_keys", stdout=io.StringIO())
        with override_settings(PII_MASTER_KEYS=[new_key]):          # bỏ key cũ: vẫn đọc được
            crypto._master.cache_clear()
            job.refresh_from_db()
            self.assertEqual(crypto.decrypt_text(job.wrapped_key, job.filename_enc), FILENAME)
        crypto._master.cache_clear()

    def test_adduser_rejects_weak_password(self):
        with mock.patch("sys.stdin", io.StringIO("123\n")):
            with self.assertRaises(Exception):
                call_command("adduser", "carol", "--password-stdin", stdout=io.StringIO())
        self.assertFalse(User.objects.filter(username="carol").exists())
        # PowerShell 5.1 chèn BOM khi pipe: mật khẩu lưu phải KHÔNG có BOM
        with mock.patch("sys.stdin", io.StringIO("﻿mot-mat-khau-du-dai\r\n")):
            call_command("adduser", "carol", "--password-stdin", stdout=io.StringIO())
        self.assertTrue(User.objects.get(username="carol").check_password("mot-mat-khau-du-dai"))


def settings_keys():
    from django.conf import settings
    return list(settings.PII_MASTER_KEYS)
