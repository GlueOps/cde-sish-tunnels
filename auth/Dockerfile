FROM python:3.14-alpine@sha256:a18b20ad69e74df6cf6d2e1b1a260d7a1ecf5380fd83b310f83ba88c461cf894

# Worker print() to a non-tty stdout is block-buffered without this — the
# auth audit lines would arrive late or vanish if a worker dies.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY auth.py .

RUN mkdir -p /data

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "auth:app"]