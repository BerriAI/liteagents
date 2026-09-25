FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g opencode-ai@1.18.29 \
    && npm cache clean --force
WORKDIR /app
COPY pyproject.toml README.md LICENSE constraints-tested.txt ./
COPY src ./src
RUN pip install --no-cache-dir '.[all,postgres]' -c constraints-tested.txt
COPY cookbook/temporal/sdk_worker.py ./worker.py
COPY deployment/worker.yaml ./worker.yaml
RUN mkdir /workspace
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/worker.py", "/app/worker.yaml", "--cwd", "/workspace"]
