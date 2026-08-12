FROM python:3.14-alpine@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc

# Worker print() to a non-tty stdout is block-buffered without this — the
# auth audit lines would arrive late or vanish if a worker dies.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY auth.py .

RUN mkdir -p /data

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "auth:app"]