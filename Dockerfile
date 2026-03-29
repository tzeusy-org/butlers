# syntax=docker/dockerfile:1.7

# Butlers application image.
#
# Requires butlers-base image (built from Dockerfile.base).
# Rebuild base only when system deps, npm CLIs, or Go source change:
#   docker build -f Dockerfile.base -t butlers-base .
#
# This image rebuilds in ~30s (Python deps + source copy only).

# --- Optional: Go builder (whatsapp-bridge) --------------------------------
# Only runs when whatsapp-bridge/ exists in context. The binary is small (~15MB)
# so we always include it rather than maintaining a separate Dockerfile.
FROM golang:1.25-bookworm AS go-builder

WORKDIR /build

COPY whatsapp-bridge/go.mod whatsapp-bridge/go.sum ./
RUN go mod download

COPY whatsapp-bridge/ ./
RUN --mount=type=cache,target=/root/.cache/go-build \
    go mod tidy && CGO_ENABLED=0 GOOS=linux go build \
    -ldflags="-s -w" \
    -o /out/whatsapp-bridge \
    ./cmd/bridge

# --- App image --------------------------------------------------------------
FROM butlers-base:latest

COPY --from=go-builder /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge

# Optional: extra dependency groups (e.g. "live-listener")
ARG EXTRAS=""

# Extra system deps for optional features
RUN if echo "$EXTRAS" | grep -q "live-listener"; then \
      apt-get update && apt-get install -y --no-install-recommends libportaudio2 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# 1. Dependency manifests (changes less often than source)
COPY pyproject.toml uv.lock ./

# 2. Source code (must be present before uv sync — local editable package)
COPY src/ src/

# 3. Install production dependencies (always include whatsapp extra — just qrcode)
#    UV_TORCH_BACKEND=cpu: use CPU-only PyTorch wheels — avoids pulling
#    NVIDIA CUDA packages that can't install in slim containers.
ENV UV_TORCH_BACKEND=cpu
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$EXTRAS" ]; then \
      uv sync --no-dev --extra whatsapp --extra "$EXTRAS"; \
    else \
      uv sync --no-dev --extra whatsapp; \
    fi

# 4. Supporting files (alembic, scripts — change rarely)
COPY alembic/alembic.ini alembic.ini
COPY alembic/ alembic/
COPY scripts/ scripts/
COPY roster/ roster/
COPY pricing.toml pricing.toml
COPY model_catalog_defaults.toml model_catalog_defaults.toml

# --frozen: don't re-sync at runtime. --no-dev: skip dev deps.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "butlers"]
CMD ["run", "--config", "/etc/butler"]
