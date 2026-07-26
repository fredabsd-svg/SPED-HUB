# ── SPED-HUB Dockerfile ──
# Multi-stage build: builder + runtime

FROM python:3.11-slim AS builder

WORKDIR /app
RUN pip install --no-cache-dir poetry

COPY pyproject.toml ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main 2>/dev/null || \
    pip install --no-cache-dir \
        sqlalchemy>=2.0 fastapi>=0.110 jinja2>=3.1 openpyxl>=3.1 \
        weasyprint>=60 pyyaml>=6.0 python-dateutil>=2.8 unidecode>=1.3 \
        python-multipart>=0.0.9 uvicorn>=0.30

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libffi-dev libcairo2 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

RUN mkdir -p /app/data

ENV SPED_HUB_DB=/app/data/sped_hub.db
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/ecds')" || exit 1

# Servidor web
CMD ["python", "-m", "uvicorn", "src.dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Para watchdog (descomente e use em conjunto com o servidor):
# CMD ["sh", "-c", "python -m uvicorn src.dashboard.app:app --host 0.0.0.0 --port 8000 & python -m src.watchdog --dir /app/uploads --db /app/data/sped_hub.db --interval 30"]