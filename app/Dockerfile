# Sorghum Protein Intelligence: one image, FastAPI serving the API and the built console.
# Node is a build-time dependency only.

FROM node:22-alpine AS console
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN mkdir -p /src/backend/sorghum_intel && npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/opt/models/fastembed \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    HOME=/home/app

# OpenCV (rapidocr, used to OCR scanned uploads) needs these.
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
  && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Bake the embedding and OCR models in so the container never downloads at runtime.
RUN mkdir -p /home/app /opt/models \
  && python -c "from fastembed import TextEmbedding as T; list(T('BAAI/bge-small-en-v1.5', cache_dir='/opt/models/fastembed').embed(['warm up']))" \
  && python -c "from rapidocr import RapidOCR; RapidOCR()" \
  && chown -R 1000:1000 /home/app /opt/models

WORKDIR /app
COPY backend/sorghum_intel /app/sorghum_intel
COPY --from=console /src/backend/sorghum_intel/static /app/sorghum_intel/static

USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "-m", "sorghum_intel", "serve"]
