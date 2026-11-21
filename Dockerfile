# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for caching
COPY Manzilcart/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY Manzilcart/ ./

# Ensure entrypoint script has Unix line endings and executable permissions
RUN sed -i 's/\r$//' ./entrypoint.sh && chmod +x ./entrypoint.sh

# Collect static files during build using Whitenoise & sqlite dummy DB
RUN DJANGO_SECRET_KEY=dummy-build-key-collectstatic-only \
    DJANGO_ENV=production \
    DJANGO_ALLOWED_HOSTS=localhost \
    DB_ENGINE=sqlite \
    python manage.py collectstatic --noinput

# Create dedicated non-root user and set permissions
RUN addgroup --system appgroup && adduser --system --group appuser && \
    mkdir -p /app/staticfiles /app/media && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "Manzilcart.asgi:application"]
