FROM docker.io/library/python:3.14.8-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 SQC_IN_CONTAINER=1
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
ARG INSTALL_POSTGRES=0
RUN if [ "$INSTALL_POSTGRES" = "1" ]; then \
      uv sync --locked --no-dev --extra postgres --no-editable; \
    else \
      uv sync --locked --no-dev --no-editable; \
    fi
COPY examples ./examples
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["run-plausibility-check"]
CMD ["--help"]
