"""Cấu hình qua biến môi trường (xem app/.env.example). Mặc định = dev local: SQLite, Celery chạy đồng bộ,
lưu file trên ổ đĩa. docker-compose đặt Postgres / Redis / MinIO."""
import os
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # app/backend
APP_DIR = BASE_DIR.parent                                   # app/ (chứa pii_core)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, default)).lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    return int(env(name, default))


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key")
if not DEBUG and SECRET_KEY == "dev-only-insecure-key":
    raise RuntimeError("đặt DJANGO_SECRET_KEY khi DJANGO_DEBUG=false")
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
    "jobs",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
# Chỉ dùng cho trang Swagger UI (/api/docs/) - API còn lại trả JSON
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "DIRS": [],
              "OPTIONS": {"context_processors": ["django.template.context_processors.request"]}}]

if env("POSTGRES_HOST"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("POSTGRES_HOST"), "PORT": env("POSTGRES_PORT", "5432"),
        "NAME": env("POSTGRES_DB", "pii"), "USER": env("POSTGRES_USER", "pii"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

# ----------------------------------------------------------------------------- DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.MultiPartParser", "rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "jobs.views.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "upload": env("UPLOAD_RATE", "60/hour"),       # mỗi user
        "login": env("LOGIN_RATE", "10/minute"),       # mỗi IP: chặn dò mật khẩu
        "review": env("REVIEW_RATE", "120/hour"),
    },
}
# Tài liệu API: /api/schema/ (OpenAPI 3) và /api/docs/ (Swagger UI - tải JS/CSS từ CDN jsdelivr;
# mạng không có internet thì chỉ dùng được /api/schema/).
SPECTACULAR_SETTINGS = {
    "TITLE": "PII Masking API",
    "DESCRIPTION": (
        "Che thông tin cá nhân (PII) tiếng Việt trong DOCX / PDF / JSON bằng Presidio + luật VN.\n\n"
        "Luồng: `POST /api/auth/token/` lấy JWT → `POST /api/jobs/` upload (202) → poll `GET /api/jobs/{id}/` tới khi "
        "`status` là `done` / `needs_review` / `failed` → `GET download_url`. Tuỳ chọn: review "
        "`GET/POST /api/jobs/{id}/review/` rồi tải lại.\n\n"
        "`needs_review` = file có thể còn PII (trang scan chưa OCR, nghi vấn sau leak-check): tải phải kèm "
        "`?confirm_review=1`."),
    "VERSION": "0.5.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SCHEMA_PATH_PREFIX": r"/api/",
    "COMPONENT_SPLIT_REQUEST": True,     # form upload hiện đúng ô chọn file trong Swagger UI
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_MINUTES", 30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=env_int("JWT_REFRESH_HOURS", 12)),
    "SIGNING_KEY": env("JWT_SIGNING_KEY", SECRET_KEY),
    "UPDATE_LAST_LOGIN": True,
}
# Throttle cần cache dùng chung giữa các process gunicorn -> Redis khi có
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("CACHE_URL")}
          if env("CACHE_URL") else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 50)
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024               # lớn hơn thì Django ghi file tạm

# ----------------------------------------------------------------------------- Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "memory://")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", CELERY_BROKER_URL == "memory://")
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_ACKS_LATE = True                               # worker chết giữa chừng -> job được chạy lại
CELERY_WORKER_PREFETCH_MULTIPLIER = 1                      # job dài: không giữ sẵn job khác
CELERY_TASK_SOFT_TIME_LIMIT = env_int("JOB_SOFT_TIME_LIMIT", 3600)   # OCR: GPU nguội ~7,5 phút + ~4 s/trang
CELERY_TASK_TIME_LIMIT = CELERY_TASK_SOFT_TIME_LIMIT + 120
CELERY_RESULT_BACKEND = None                               # trạng thái job nằm ở Postgres, không ở Redis
CELERY_TASK_IGNORE_RESULT = True
CELERY_BEAT_SCHEDULE = {
    "cleanup-expired": {"task": "jobs.tasks.cleanup_expired", "schedule": timedelta(minutes=30)},
}

# ----------------------------------------------------------------------------- lưu trữ & mã hoá
STORAGE_BACKEND = env("STORAGE_BACKEND", "local")           # local | s3 (MinIO)
STORAGE_LOCAL_ROOT = Path(env("STORAGE_LOCAL_ROOT", BASE_DIR / "storage"))
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL")                    # vd http://minio:9000
S3_ACCESS_KEY = env("S3_ACCESS_KEY")
S3_SECRET_KEY = env("S3_SECRET_KEY")
S3_BUCKET = env("S3_BUCKET", "pii-masking")
S3_REGION = env("S3_REGION", "us-east-1")
# Master key (Fernet, base64 32 byte): bọc khoá riêng của từng job. Sinh: python -c "from cryptography.fernet
# import Fernet; print(Fernet.generate_key().decode())". Mất key = mất mọi file gốc / mapping đã lưu.
# Đổi key: PII_MASTER_KEYS="key_mới,key_cũ" -> manage.py rewrap_keys -> bỏ key_cũ khỏi danh sách.
PII_MASTER_KEYS = [k.strip() for k in (env("PII_MASTER_KEYS") or env("PII_MASTER_KEY") or "").split(",") if k.strip()]
if not PII_MASTER_KEYS:
    if not DEBUG:
        raise RuntimeError("đặt PII_MASTER_KEY (hoặc PII_MASTER_KEYS)")
    PII_MASTER_KEYS = ["ZGV2LW9ubHktbWFzdGVyLWtleS0zMi1ieXRlcy0hISE="]   # dev: KHÔNG dùng cho dữ liệu thật

ORIGINAL_TTL_HOURS = env_int("ORIGINAL_TTL_HOURS", 24)      # file gốc + kết quả OCR + segments
OUTPUT_TTL_DAYS = env_int("OUTPUT_TTL_DAYS", 30)            # file đã che + mapping

# ----------------------------------------------------------------------------- pii_core
PII_ANALYZER_CONFIG = env("PII_ANALYZER_CONFIG", "D")
OCR_ENABLED = env_bool("OCR_ENABLED", bool(env("OCR_KEY")))  # OCR_URL / OCR_KEY đọc trong pii_core.ocr

# ----------------------------------------------------------------------------- log: KHÔNG ghi text / PII
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"presidio-analyzer": {"level": "ERROR"}, "presidio-anonymizer": {"level": "ERROR"}},
}
