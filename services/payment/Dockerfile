FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN useradd -m -u 1001 svcuser && chown -R svcuser /app
USER svcuser
EXPOSE 5003
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD curl -f http://localhost:5003/health || exit 1
CMD ["python", "app.py"]