# Allora Platform Bundle Dockerfile Template
#
# This Dockerfile is used by the platform to build containers for user model bundles.
# The platform will substitute template variables before building.
#
# Template variables:
#   {{PYTHON_VERSION}} - Python version from model.yaml (e.g., "3.11")
#   {{BUNDLE_ARCHIVE}} - Path to the user's bundle .tar.gz file
#   {{BUNDLE_NAME}} - Name of the extracted bundle directory

# Use slim Python image for smaller size
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

# Install system dependencies commonly needed by ML packages
# This is a "fat" base image approach that supports most pure-Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 allora && \
    mkdir -p /workspace && \
    chown -R allora:allora /workspace

# Set working directory
WORKDIR /workspace

# Copy and extract the user's bundle
COPY {{BUNDLE_ARCHIVE}} /tmp/bundle.tar.gz
RUN tar -xzf /tmp/bundle.tar.gz -C /workspace && \
    rm /tmp/bundle.tar.gz && \
    chown -R allora:allora /workspace

# Install Allora SDK
RUN pip install --no-cache-dir allora-sdk

# Install user's dependencies from bundle
RUN pip install --no-cache-dir -r /workspace/{{BUNDLE_NAME}}/requirements.txt

# Copy platform bootstrap script
COPY bootstrap.py /workspace/bootstrap.py
RUN chown allora:allora /workspace/bootstrap.py

# Switch to non-root user
USER allora

# Set environment variables
ENV BUNDLE_ROOT=/workspace/{{BUNDLE_NAME}}
ENV PYTHONUNBUFFERED=1

# Run the bootstrap script
CMD ["python", "/workspace/bootstrap.py"]
