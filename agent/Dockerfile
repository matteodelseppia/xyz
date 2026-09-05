FROM node:22.15.0-bookworm-slim AS node
FROM python:3.12.8-slim-bookworm AS runtime

COPY --from=node /usr/local/ /usr/local/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NODE_ENV=production
RUN node -e "if (typeof require('node:zlib').createZstdDecompress !== 'function') process.exit(1)"
RUN npm install --global @earendil-works/pi-coding-agent@0.84.4
WORKDIR /app
COPY package.json package-lock.json ./
COPY pi-extension/package.json pi-extension/package.json
COPY web/package.json web/package.json
RUN npm ci --omit=dev --workspace pi-extension
COPY agent ./agent
RUN pip install --no-cache-dir ./agent
COPY config ./config
COPY prompts ./prompts
COPY templates ./templates
COPY web/public/loved-ones/index.html ./web/public/loved-ones/index.html
COPY pi-extension/src ./pi-extension/src
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
USER app
ENV XYZ_STORAGE_ROOT=/data XYZ_REPOSITORY_ROOT=/app
ENTRYPOINT ["xyz-agent"]
