# Dockerfile for Butlers AI agent framework
#
# Multi-stage build:
#   Stage 1 (go-builder): Compiles the whatsapp-bridge Go binary (CGO_ENABLED=0, stripped).
#   Stage 2 (final): Python runtime with the compiled binary copied in.
#
# Build caching: the go-builder stage is only invalidated when whatsapp-bridge/ changes.
# Changing Python source (src/, pyproject.toml) does NOT trigger a Go rebuild.

# --- Stage 1: Go builder ---------------------------------------------------
FROM golang:1.24-bookworm AS go-builder

WORKDIR /build

# Copy Go module files first for layer caching.
COPY whatsapp-bridge/go.mod whatsapp-bridge/go.sum ./

# Download dependencies (cached unless go.mod/go.sum change).
RUN go mod download

# Copy Go source.
COPY whatsapp-bridge/ ./

# Compile statically linked binary (no CGO, stripped for smaller size ~15-20 MB).
RUN CGO_ENABLED=0 GOOS=linux go build \
    -ldflags="-s -w" \
    -o /out/whatsapp-bridge \
    ./cmd/bridge

# --- Stage 2: Python runtime ------------------------------------------------
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

# Install LLM runtime CLIs globally.
# Butlers can use any of these as runtime adapters (configured per-butler in butler.toml).
RUN npm install -g @openai/codex opencode-ai @anthropic-ai/claude-code @google/gemini-cli

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy the compiled whatsapp-bridge binary from the builder stage.
COPY --from=go-builder /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge

# Set working directory
WORKDIR /app

# Optional: extra dependency groups (e.g. "live-listener" for audio connector, "whatsapp" for WhatsApp)
ARG EXTRAS=""

# Install extra system dependencies for optional extras
RUN if echo "$EXTRAS" | grep -q "live-listener"; then \
      apt-get update && apt-get install -y libportaudio2 && rm -rf /var/lib/apt/lists/*; \
    fi

# Copy project files (source must be present before uv sync because
# pyproject.toml declares butlers as a local editable package)
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Install production dependencies
RUN if [ -n "$EXTRAS" ]; then \
      uv sync --no-dev --extra "$EXTRAS"; \
    else \
      uv sync --no-dev; \
    fi

# Copy remaining files (alembic, scripts)
COPY alembic/alembic.ini alembic.ini
COPY alembic/ alembic/
COPY scripts/ scripts/

# Set entrypoint and default command.
# --frozen prevents uv from re-syncing deps at runtime (they're already installed).
# --no-dev ensures dev dependencies aren't pulled in.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "butlers"]
CMD ["run", "--config", "/etc/butler"]
