import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import type { Storage, StoredObject } from "./storage";
import { storageFromEnvironment } from "./storage";

type Pointer = { schema_version: number; date: string; run_prefix: string; manifest_sha256: string };
type Manifest = { schema_version: number; run_id: string; publication_date: string; artifacts: Array<{ path: string; sha256: string; content_type: string }> };
type CacheEntry = { value: StoredObject; expiresAt: number };

const POINTER_TTL = 30_000;
const HTML_TTL = 60_000;
const SUPPORTED_SCHEMAS = new Set([1]);
const cache = new Map<string, CacheEntry>();
const STATIC_ROOT = process.env.XYZ_STATIC_ROOT ?? fileURLToPath(new URL("../public", import.meta.url));
const STATIC_ROUTES: Record<string, { path: string; contentType: string; cacheControl: string }> = {
  "/loved-ones": { path: "loved-ones/index.html", contentType: "text/html; charset=utf-8", cacheControl: "no-cache" },
  "/loved-ones/": { path: "loved-ones/index.html", contentType: "text/html; charset=utf-8", cacheControl: "no-cache" },
  "/loved-ones/site.css": { path: "loved-ones/site.css", contentType: "text/css; charset=utf-8", cacheControl: "no-cache" },
  "/loved-ones/filter.js": { path: "loved-ones/filter.js", contentType: "application/javascript; charset=utf-8", cacheControl: "no-cache" },
};

function securityHeaders(cacheControl: string, requestId: string): Headers {
  return new Headers({
    "cache-control": cacheControl,
    "content-security-policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-request-id": requestId,
  });
}

async function get(storage: Storage, key: string, ttl: number): Promise<StoredObject | null> {
  const existing = cache.get(key);
  if (existing && existing.expiresAt > Date.now()) return existing.value;
  try {
    const value = await storage.get(key);
    if (value) cache.set(key, { value, expiresAt: Date.now() + ttl });
    return value;
  } catch (error) {
    if (existing) return existing.value;
    throw error;
  }
}

function json<T>(object: StoredObject): T {
  return JSON.parse(new TextDecoder().decode(object.data)) as T;
}
function sha256(data: Uint8Array): string { return createHash("sha256").update(data).digest("hex"); }
function validPrefix(pointer: Pointer): boolean {
  return SUPPORTED_SCHEMAS.has(pointer.schema_version) &&
    pointer.run_prefix === `runs/${pointer.date}/${pointer.run_prefix.split("/")[2]}` &&
    /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(pointer.date) && /^[a-f0-9]{32}$/.test(pointer.run_prefix.split("/")[2] ?? "");
}

async function loadPointer(storage: Storage, key: string): Promise<Pointer | null> {
  const object = await get(storage, key, POINTER_TTL);
  if (!object) return null;
  const pointer = json<Pointer>(object);
  return validPrefix(pointer) ? pointer : null;
}

async function loadManifest(storage: Storage, pointer: Pointer): Promise<Manifest | null> {
  const object = await get(storage, `${pointer.run_prefix}/manifest.json`, POINTER_TTL);
  if (!object || sha256(object.data) !== pointer.manifest_sha256) return null;
  const manifest = json<Manifest>(object);
  if (!SUPPORTED_SCHEMAS.has(manifest.schema_version) || manifest.run_id !== pointer.run_prefix.split("/")[2] || manifest.publication_date !== pointer.date) return null;
  return manifest;
}

function response(body: Uint8Array | string, status: number, type: string, cacheControl: string, requestId: string): Response {
  const headers = securityHeaders(cacheControl, requestId);
  headers.set("content-type", type);
  const responseBody = typeof body === "string"
    ? body
    : body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer;
  return new Response(responseBody, { status, headers });
}
function error(status: 404 | 503, requestId: string): Response {
  return response(status === 404 ? "Not found\n" : "Service unavailable\n", status, "text/plain; charset=utf-8", "no-store", requestId);
}

async function staticResponse(
  route: { path: string; contentType: string; cacheControl: string }, request: Request, requestId: string,
): Promise<Response> {
  const body = request.method === "HEAD" ? new Uint8Array() : await readFile(join(STATIC_ROOT, route.path));
  return response(body, 200, route.contentType, route.cacheControl, requestId);
}

export function createHandler(storage: Storage): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    const started = performance.now();
    const requestId = randomUUID();
    const url = new URL(request.url);
    let status = 500;
    try {
      if (request.method !== "GET" && request.method !== "HEAD") { status = 404; return error(404, requestId); }
      const staticRoute = STATIC_ROUTES[url.pathname];
      if (staticRoute) {
        status = 200;
        return await staticResponse(staticRoute, request, requestId);
      }
      if (url.pathname === "/health") {
        const pointer = await loadPointer(storage, "current.json");
        const manifest = pointer ? await loadManifest(storage, pointer) : null;
        status = pointer && manifest ? 200 : 503;
        return response(JSON.stringify({ ok: status === 200 }), status, "application/json", "no-store", requestId);
      }
      let pointer: Pointer | null = null;
      let htmlPath: "index.html" | "prompt/index.html" | "sources/index.html" | null = null;
      if (url.pathname === "/") { pointer = await loadPointer(storage, "current.json"); htmlPath = "index.html"; }
      else if (url.pathname === "/sources" || url.pathname === "/sources/") { pointer = await loadPointer(storage, "current.json"); htmlPath = "sources/index.html"; }
      else {
        const day = url.pathname.match(/^\/days\/(\d{4}-\d{2}-\d{2})\/$/);
        const prompt = url.pathname.match(/^\/(?:days\/)?(\d{4}-\d{2}-\d{2})\/prompt\/$/);
        if (day) { pointer = await loadPointer(storage, `days/${day[1]}.json`); htmlPath = "index.html"; }
        if (prompt) { pointer = await loadPointer(storage, `days/${prompt[1]}.json`); htmlPath = "prompt/index.html"; }
      }
      if (pointer && htmlPath) {
        const manifest = await loadManifest(storage, pointer);
        if (!manifest) { status = 503; return error(503, requestId); }
        const artifact = manifest.artifacts.find(item => item.path === htmlPath);
        const object = artifact ? await get(storage, `${pointer.run_prefix}/${htmlPath}`, HTML_TTL) : null;
        if (!object || sha256(object.data) !== artifact?.sha256) { status = 503; return error(503, requestId); }
        status = 200;
        return response(request.method === "HEAD" ? new Uint8Array() : object.data, 200, "text/html; charset=utf-8", "public, max-age=60", requestId);
      }
      const asset = url.pathname.match(/^\/assets\/(\d{4}-\d{2}-\d{2})\/([a-f0-9]{32})\/([a-f0-9]{64})\.(css|js|svg)$/);
      if (asset) {
        const pointerForDay = await loadPointer(storage, `days/${asset[1]}.json`);
        if (!pointerForDay || pointerForDay.run_prefix !== `runs/${asset[1]}/${asset[2]}`) { status = 404; return error(404, requestId); }
        const manifest = await loadManifest(storage, pointerForDay);
        const relative = `assets/${asset[3]}.${asset[4]}`;
        const contentTypes: Record<string, string> = { css: "text/css; charset=utf-8", js: "application/javascript; charset=utf-8", svg: "image/svg+xml" };
        const contentType = contentTypes[asset[4] ?? ""];
        const artifact = manifest?.artifacts.find(item => item.path === relative && !!contentType && item.content_type.startsWith(contentType.split(";")[0] ?? contentType));
        const object = artifact ? await get(storage, `${pointerForDay.run_prefix}/${relative}`, 31_536_000_000) : null;
        if (!contentType || !object || sha256(object.data) !== artifact?.sha256) { status = 404; return error(404, requestId); }
        status = 200;
        return response(request.method === "HEAD" ? new Uint8Array() : object.data, 200, contentType, "public, max-age=31536000, immutable", requestId);
      }
      status = 404;
      return error(404, requestId);
    } catch {
      status = 503;
      return error(503, requestId);
    } finally {
      console.log(JSON.stringify({ request_id: requestId, route: url.pathname, status, duration_ms: Math.round((performance.now() - started) * 100) / 100 }));
    }
  };
}

if (import.meta.main) {
  const port = Number(process.env.PORT ?? "3000");
  Bun.serve({ port, fetch: createHandler(storageFromEnvironment()) });
  console.log(JSON.stringify({ event: "server_started", port }));
}
