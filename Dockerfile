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

CMD ["bash", "start.sh"]
