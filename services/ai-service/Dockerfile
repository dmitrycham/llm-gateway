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
RUN useradd --system --uid 10001 --no-create-home app
# Only the venv: --no-editable installed the package into it, the source is not needed.
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["uvicorn", "ai_service.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
