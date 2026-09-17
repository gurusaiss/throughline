# Multi-stage build: a slim runtime image with no build toolchain.
FROM python:3.11-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY app ./app
RUN uv pip install --system --no-cache .

FROM python:3.11-slim AS runtime

# Non-root user: a compromised process should not have write access to its own image layers.
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

USER appuser
EXPOSE 8000

# Migrations run as an explicit, separate step (docker-compose `command` override or an init container),
# never automatically on container start — an auto-migrating web process is how you corrupt a schema
# under concurrent rollout.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
