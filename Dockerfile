FROM python:3.12-slim

WORKDIR /app

COPY . /app
RUN pip install --no-cache-dir . \
    && adduser --disabled-password --no-create-home worker

USER worker

ENTRYPOINT ["allora-worker"]
