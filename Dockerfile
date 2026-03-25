# Dockerfile for Butlers AI agent framework
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install claude-code globally via npm
RUN npm install -g claude-code

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Set working directory
WORKDIR /app

# Optional: extra dependency groups (e.g. "live-listener" for audio connector)
ARG EXTRAS=""

# Install extra system dependencies for optional extras
RUN if echo "$EXTRAS" | grep -q "live-listener"; then \
      apt-get update && apt-get install -y libportaudio2 && rm -rf /var/lib/apt/lists/*; \
    fi

# Copy dependency manifests first for better layer caching —
# source changes won't invalidate the dependency install layer.
COPY pyproject.toml uv.lock ./

# Install production dependencies (cached unless pyproject.toml or uv.lock change)
RUN if [ -n "$EXTRAS" ]; then \
      uv sync --no-dev --extra "$EXTRAS"; \
    else \
      uv sync --no-dev; \
    fi

# Copy application code and supporting files
COPY src/ src/
COPY alembic/alembic.ini alembic.ini
COPY alembic/ alembic/
COPY scripts/ scripts/

# Set entrypoint and default command
ENTRYPOINT ["uv", "run", "butlers"]
CMD ["run", "--config", "/etc/butler"]
