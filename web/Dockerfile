FROM oven/bun:1.2.23-alpine AS build
WORKDIR /app
COPY web/package.json web/tsconfig.json ./
COPY web/src ./src
RUN bun build src/server.ts --target=bun --outfile=dist/server.js

FROM oven/bun:1.2.23-alpine
WORKDIR /app
COPY --from=build /app/dist/server.js ./server.js
COPY web/public ./public
RUN mkdir -p /data && chown -R bun:bun /app /data
USER bun
ENV PORT=3000 XYZ_STORAGE_ROOT=/data XYZ_STATIC_ROOT=/app/public
EXPOSE 3000
CMD ["bun", "run", "server.js"]
