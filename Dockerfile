FROM python:3.11-slim

# Force glibc to limit memory arenas to reduce native memory fragmentation (RSS bloat)
ENV MALLOC_ARENA_MAX=2
ENV PYTHONPATH="/app:/app/app"
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    wget \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=12 \
  CMD curl -sf http://127.0.0.1:8000/health || wget -qO- http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["bash", "start.sh"]
