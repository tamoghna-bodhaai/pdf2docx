# Railway builds from this. Everything the application needs ships as a
# manylinux wheel — PyMuPDF included — so there is no apt layer here.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# No PDF2DOCX_DATA_DIR here, deliberately. Hardcoding /data made a deployment
# with no volume attached look configured for durable storage: the directory
# exists either way, so nothing could tell the difference, and the accounts were
# gone by the time anyone noticed. The path is read from
# RAILWAY_VOLUME_MOUNT_PATH instead, which Railway sets only when a volume is
# really there — and app/storage.py refuses to boot when it is not.

WORKDIR /srv

# Ahead of the source, so a code change does not reinstall the dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# `--proxy-headers` so the app sees the scheme the browser actually used;
# Railway terminates TLS in front of it.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
