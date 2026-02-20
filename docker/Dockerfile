# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Base OS dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    make \
    perl \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 (required for codex/opencode/claude-code CLIs).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install AI runtime CLIs used by this repo.
RUN npm install -g @openai/codex claude-code opencode-ai

# Install uv package manager.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy repo files.
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY tests ./tests
COPY roster ./roster
COPY frontend ./frontend
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY docker-compose.yml ./docker-compose.yml
COPY dev.sh ./dev.sh
COPY Makefile ./Makefile
COPY AGENTS.md ./AGENTS.md

# Install runtime + dev Python dependencies.
RUN uv sync --frozen --dev \
    && chmod +x ./dev.sh

# Dev container default shell.
CMD ["bash"]
