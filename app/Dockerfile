# Same Python version as local development; slim has ready-made wheels for asyncpg/uvloop
FROM python:3.12-slim

# Unbuffered output so every log line reaches Docker (and CloudWatch) immediately
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes
COPY requirements.txt .
RUN pip install -r requirements.txt

# Then the code, which changes often
COPY . .

# Run as a normal user, not root
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000

# Docker marks the container unhealthy after 3 failed checks of /health
# (slim has no curl, so use Python's built-in urllib; a 503 raises and exits non-zero)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# One worker on purpose: the checker runs inside the app, more workers would duplicate it
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
