# syntax=docker/dockerfile:1.7

# Required immutable runtime/CLI base from the coordinated source build.
# No local tag or invented digest is a fallback. BuildKit also requires the
# explicitly supplied named python-artifacts context (see deployment docs).
ARG BUTLERS_BASE_IMAGE

# Retain the original WhatsApp bridge source build and dependency lock.
# Digest-pinned for full reproducibility (Go 1.25.11).
FROM golang:1.25.11-bookworm@sha256:bbb255b0e131db500cf0520adc97441d2260cf629c7fa7e39e025ddf53995a24 AS go-builder

WORKDIR /build

COPY whatsapp-bridge/go.mod whatsapp-bridge/go.sum ./
RUN go mod download

COPY whatsapp-bridge/ ./
RUN --mount=type=cache,target=/root/.cache/go-build \
    go mod tidy && CGO_ENABLED=0 GOOS=linux go build \
    -ldflags="-s -w" \
    -o /out/whatsapp-bridge \
    ./cmd/bridge

# --- Shared application source (no installed environment yet) ----------------
FROM ${BUTLERS_BASE_IMAGE} AS application-source
ARG BUTLERS_BASE_IMAGE
ARG EXTRAS=""
WORKDIR /app

RUN /usr/local/bin/python - <<'PYBASE'
import os, platform, re, sys
value = os.environ.get("BUTLERS_BASE_IMAGE", "")
if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}", value):
    raise SystemExit("BUTLERS_BASE_IMAGE must be a full registry/repository@sha256 reference")
if (sys.version_info[:3], platform.system(), platform.machine(), platform.libc_ver()) != (
    (3, 12, 13), "Linux", "x86_64", ("glibc", "2.41")
):
    raise SystemExit("runtime base differs from Python3.12.13/linux-amd64/glibc2.41")
PYBASE
RUN test "$(uv --version)" = "uv 0.12.22 (x86_64-unknown-linux-gnu)"

# libportaudio2 for selected live-listener is part of the immutable base, never
# an app/startup apt download. Both targets install exactly EXTRAS below.
COPY --from=go-builder /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY alembic/alembic.ini alembic.ini
COPY alembic/ alembic/
COPY scripts/ scripts/
COPY roster/ roster/
COPY pricing.toml pricing.toml
COPY model_catalog_defaults.toml model_catalog_defaults.toml

# --- Complete, verified offline installation inputs -------------------------
FROM application-source AS installation-inputs
COPY --from=python-artifacts / /opt/python-artifacts/
ENV UV_OFFLINE=1 \
    UV_NO_INDEX=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_FIND_LINKS=/opt/python-artifacts/wheelhouse \
    UV_CACHE_DIR=/opt/python-artifacts/uv-cache

FROM installation-inputs AS production-install
RUN --network=none <<'INSTALL'
set -efu
set --
for extra in $EXTRAS; do set -- "$@" --extra "$extra"; done
/usr/local/bin/python -I scripts/runtime_clis/python_entrypoint.py verify-build \
  --artifacts /opt/python-artifacts --project /app --profile production "$@"
uv sync --frozen --offline --no-dev "$@"
mkdir -p /usr/local/share/butlers
/usr/local/bin/python -I scripts/runtime_clis/python_environment.py seal \
  --environment /app/.venv --project /app \
  --manifest /usr/local/share/butlers/python-environment.json "$@"
INSTALL

FROM installation-inputs AS development-install
RUN --network=none <<'INSTALL'
set -efu
set --
for extra in $EXTRAS; do set -- "$@" --extra "$extra"; done
/usr/local/bin/python -I scripts/runtime_clis/python_entrypoint.py verify-build \
  --artifacts /opt/python-artifacts --project /app --profile development "$@"
uv sync --frozen --offline --dev "$@"
mkdir -p /usr/local/share/butlers
/usr/local/bin/python -I scripts/runtime_clis/python_environment.py seal \
  --environment /app/.venv --project /app --group dev \
  --manifest /usr/local/share/butlers/python-environment.json "$@"
INSTALL

# Final images retain source and the real environment, not wheels/cache/build
# constraints. Editable /app/src supports mounted development source changes.
FROM application-source AS development
COPY --from=development-install /app/.venv /app/.venv
COPY --from=development-install /usr/local/share/butlers/python-environment.json /usr/local/share/butlers/python-environment.json
ARG GIT_SHA="unknown"
ENV GIT_SHA=${GIT_SHA}
ENTRYPOINT ["/usr/local/bin/python", "-I", "/app/scripts/runtime_clis/python_entrypoint.py", "butlers"]
CMD ["run", "--config", "/etc/butler"]

# The ordinary default target remains production, with no dev group installed.
FROM application-source AS production
COPY --from=production-install /app/.venv /app/.venv
COPY --from=production-install /usr/local/share/butlers/python-environment.json /usr/local/share/butlers/python-environment.json
ARG GIT_SHA="unknown"
ENV GIT_SHA=${GIT_SHA}
ENTRYPOINT ["/usr/local/bin/python", "-I", "/app/scripts/runtime_clis/python_entrypoint.py", "butlers"]
CMD ["run", "--config", "/etc/butler"]
