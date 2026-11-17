FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY src /app/src
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --extra postgres --extra temporal --extra agent
ENV PATH="/app/.venv/bin:${PATH}"
ENV CLUSTER_DATA_DIR=/data CLUSTER_REPO_ROOT=/repos PYTHONUNBUFFERED=1
EXPOSE 8787
ENTRYPOINT ["research-cluster"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8787"]
