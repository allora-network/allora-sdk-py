FROM python:3.12-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock* README.md ./
COPY src/ src/

RUN pip install --no-cache-dir .

FROM python:3.12-slim

RUN adduser --disabled-password --no-create-home --gecos "" worker

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/allora-worker /usr/local/bin/allora-worker

WORKDIR /app

USER worker

ENTRYPOINT ["allora-worker"]
