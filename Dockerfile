FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/backend \
    PIP_NO_CACHE_DIR=1 \
    # font có dấu tiếng Việt cho trang PDF dựng lại từ OCR (pii_core/documents/rebuild.py)
    PII_FONT_DIR=/usr/share/fonts/truetype/dejavu \
    PII_FONT_REGULAR=DejaVuSans.ttf \
    PII_FONT_BOLD=DejaVuSans-Bold.ttf

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-backend.txt ./
RUN pip install -r requirements.txt -r requirements-backend.txt

COPY pii_core ./pii_core
COPY backend ./backend

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app
WORKDIR /app/backend
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
