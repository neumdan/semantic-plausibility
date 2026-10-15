FROM docker.io/library/python:3.14.8-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1 PYTHONUNBUFFERED=1 SQC_IN_CONTAINER=1
COPY pyproject.toml uv.lock ./
ARG INSTALL_POSTGRES=0
# Keep dependency installation cached when source/docs/examples change.
RUN if [ "$INSTALL_POSTGRES" = "1" ]; then \
      uv sync --locked --no-dev --extra postgres --no-install-project; \
    else \
      uv sync --locked --no-dev --no-install-project; \
    fi
COPY README.md LICENSE NOTICE ./
COPY src ./src
RUN if [ "$INSTALL_POSTGRES" = "1" ]; then \
      uv sync --locked --no-dev --extra postgres --no-editable; \
    else \
      uv sync --locked --no-dev --no-editable; \
    fi
COPY examples ./examples
COPY terminology.example.toml ./
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["run-plausibility-check"]
CMD ["--help"]
