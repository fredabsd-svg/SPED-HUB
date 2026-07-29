# ── SPED-HUB Dockerfile ──
# Multi-stage build: builder + runtime

FROM python:3.11-slim AS builder

WORKDIR /app
RUN pip install --no-cache-dir poetry

COPY pyproject.toml ./
# O fallback do pip precisa das aspas: sem elas o shell lê `>=` como
# redirecionamento de saída, descarta a constraint e ainda cria arquivos
# chamados `=2.0`.  A lista também estava desatualizada — faltavam
# strawberry-graphql, redis e playwright, então GraphQL e cache quebravam
# quando o poetry falhava.
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main 2>/dev/null || \
    pip install --no-cache-dir \
        "sqlalchemy>=2.0" "fastapi>=0.110" "jinja2>=3.1" "openpyxl>=3.1" \
        "weasyprint>=60" "pyyaml>=6.0" "python-dateutil>=2.8" "unidecode>=1.3" \
        "python-multipart>=0.0.9" "uvicorn>=0.30" "strawberry-graphql>=0.200" \
        "redis>=5.0" "playwright>=1.40"

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libffi-dev libcairo2 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

RUN mkdir -p /app/data /app/uploads

ENV DATABASE_URL=sqlite:////app/data/sped_hub.db
# Precisa apontar para o volume compartilhado com o worker: o container web
# grava o upload e o worker lê o mesmo arquivo.  Sem isto cada container
# usava um diretório próprio e a importação assíncrona nunca encontrava o
# arquivo enviado.
ENV SPED_HUB_UPLOAD_DIR=/app/uploads
ENV PYTHONPATH=/app

EXPOSE 8000

# /api/ecds passou a exigir autenticação, então o healthcheck antigo recebia
# 401 e marcava o container como unhealthy para sempre.  /api/v1/health é o
# endpoint público de saúde.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Servidor web
CMD ["python", "-m", "uvicorn", "src.dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Para watchdog (descomente e use em conjunto com o servidor):
# CMD ["sh", "-c", "python -m uvicorn src.dashboard.app:app --host 0.0.0.0 --port 8000 & python -m src.watchdog --dir /app/uploads --db /app/data/sped_hub.db --interval 30"]