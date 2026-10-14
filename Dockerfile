# The console in a container, for a machine that has Docker and nothing else.
#
# The project folder is mounted at /app at run time, so the configuration,
# the scoring brief, the templates and the data stay on the host and survive
# a rebuild; the virtual environment lives outside it, under /opt/venv, so the
# mount cannot hide it. Model access inside the container is by API key: the
# `claude` command and its sign-in are per machine and are not here.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    ARPENTEUR_HOME=/app

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY arpenteur ./arpenteur
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "--frozen", "arpenteur", "console", "--host", "0.0.0.0", "--port", "8000"]
