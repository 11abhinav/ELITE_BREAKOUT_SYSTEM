FROM python:3.11-slim

# Force glibc to limit memory arenas to reduce native memory fragmentation (RSS bloat)
ENV MALLOC_ARENA_MAX=2
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "app/main.py"]
