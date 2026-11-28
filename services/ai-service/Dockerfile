# syntax=docker/dockerfile:1
FROM python:3.14.10-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv
# System Python in both stages, so the venv's interpreter links stay valid after the copy.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first: this layer is rebuilt only when the lock file changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project --no-editable
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14.10-slim
# Trivy gate (LGW-27): the newest base tag still ships libzstd1 1.5.7+dfsg-1 (fixed in +deb13u1)
# and system pip, whose vendored urllib3/msgpack/setuptools have fixable HIGH CVEs. The runtime
# only runs uvicorn from /app/.venv, so pip is not needed. The libzstd1 upgrade does nothing once
# the base image includes the fix.
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade libzstd1 \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y pip \
 && useradd --system --uid 10001 --no-create-home app
# Only the venv: --no-editable installed the package into it, the source is not needed.
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["uvicorn", "ai_service.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
