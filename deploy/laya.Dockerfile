FROM docker.io/library/python:3.14.8-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_NO_CACHE=1 PYTHONUNBUFFERED=1 SQC_IN_CONTAINER=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra decision --no-install-project
COPY README.md LICENSE NOTICE catalog.csv ./
COPY src ./src
RUN uv sync --locked --no-dev --extra decision --no-editable
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["run-plausibility-check"]
CMD ["decision-serve", "--config", "/app/pipeline.toml"]
