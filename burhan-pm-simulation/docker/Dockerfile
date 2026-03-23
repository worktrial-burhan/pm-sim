FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /workspace

COPY pyproject.toml README.md ./
COPY app ./app
COPY docs ./docs
COPY scripts ./scripts

RUN uv pip install --system -e .

RUN useradd -m -s /bin/bash simuser \
    && mkdir -p /home/simuser/.claude/projects \
    && chown -R simuser:simuser /home/simuser/.claude /workspace

USER simuser

CMD ["pm-sim-api"]
