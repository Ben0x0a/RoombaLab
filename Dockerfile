FROM python:3.12-slim-bookworm
COPY --from=astral/uv:0.12.13 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Dependencies first (cached layer), resolved exactly from uv.lock.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY public ./public
RUN uv sync --frozen --no-dev && mkdir -p /data
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "roombalab.server"]
