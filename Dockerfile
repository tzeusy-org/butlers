# Butlers application image.
#
# Requires butlers-base image (built from Dockerfile.base).
# Rebuild base only when system deps, npm CLIs, or Go source change:
#   docker build -f Dockerfile.base -t butlers-base .
#
# This image rebuilds in ~30s (Python deps + source copy only).

FROM butlers-base:latest

# Optional: extra dependency groups (e.g. "live-listener", "whatsapp")
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

# 3. Install production dependencies
RUN if [ -n "$EXTRAS" ]; then \
      uv sync --no-dev --extra "$EXTRAS"; \
    else \
      uv sync --no-dev; \
    fi

# 4. Supporting files (alembic, scripts — change rarely)
COPY alembic/alembic.ini alembic.ini
COPY alembic/ alembic/
COPY scripts/ scripts/
COPY roster/ roster/
COPY pricing.toml pricing.toml

# --frozen: don't re-sync at runtime. --no-dev: skip dev deps.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "butlers"]
CMD ["run", "--config", "/etc/butler"]
