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

# Copy project files
COPY pyproject.toml .
COPY src/ src/
COPY alembic/alembic.ini alembic.ini
COPY alembic/ alembic/

# Install production dependencies
RUN uv sync --no-dev

# Set entrypoint and default command
ENTRYPOINT ["uv", "run", "butlers"]
CMD ["run", "--config", "/etc/butler"]
