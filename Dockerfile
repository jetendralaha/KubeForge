FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install Python package
COPY pyproject.toml .
COPY src/ src/
COPY migrations/ migrations/
RUN pip install --no-cache-dir .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

CMD ["kubeforge", "serve"]
