FROM python:3.11-slim

# Force glibc to limit memory arenas to reduce native memory fragmentation (RSS bloat)
ENV MALLOC_ARENA_MAX=2
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

HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=10 \
  CMD wget -q --spider http://127.0.0.1:8000/health || wget -q --spider http://127.0.0.1:8080/health || exit 1

CMD ["bash", "start.sh"]
