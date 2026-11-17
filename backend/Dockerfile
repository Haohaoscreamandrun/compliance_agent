# cSpell:words mupdf uvicorn adduser appuser
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create non-root runtime user and ensure /app/storage & /app/secrets exist with correct ownership
RUN adduser --disabled-password --gecos "" --uid 1000 appuser \
    && mkdir -p /app/storage /app/secrets \
    && chown -R appuser:appuser /app

COPY --chown=appuser:appuser pyproject.toml ./
COPY --chown=appuser:appuser app ./app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
