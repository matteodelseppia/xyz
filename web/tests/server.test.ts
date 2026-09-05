import { createHash } from "node:crypto";
import { expect, test } from "bun:test";
import { createHandler } from "../src/server";
import { virtualHostEndpoint } from "../src/storage";
import type { Storage, StoredObject } from "../src/storage";

class MemoryStorage implements Storage {
  constructor(readonly objects: Map<string, StoredObject>) {}
  async get(key: string): Promise<StoredObject | null> { return this.objects.get(key) ?? null; }
}
const bytes = (value: string) => new TextEncoder().encode(value);
const hash = (value: Uint8Array) => createHash("sha256").update(value).digest("hex");

test("constructs the virtual-hosted Railway S3 endpoint", () => {
  expect(virtualHostEndpoint("https://t3.storageapi.dev", "bucket-abc")).toBe(
    "https://bucket-abc.t3.storageapi.dev/",
  );
  expect(virtualHostEndpoint("https://bucket-abc.t3.storageapi.dev", "bucket-abc")).toBe(
    "https://bucket-abc.t3.storageapi.dev/",
  );
});

test("serves the static loved ones page and its filter assets", async () => {
  const handle = createHandler(new MemoryStorage(new Map()));
  const page = await handle(new Request("http://site/loved-ones/?tag=delivery"));
  expect(page.status).toBe(200);
  expect(page.headers.get("content-type")).toContain("text/html");
  expect(await page.text()).toContain("How I ship projects at big tech companies");
  const script = await handle(new Request("http://site/loved-ones/filter.js"));
  expect(script.status).toBe(200);
  expect(script.headers.get("content-security-policy")).toContain("script-src 'self'");
  expect(await script.text()).toContain("URLSearchParams");
});

test("serves only pointer-referenced, hash-verified artifacts", async () => {
  const day = "2026-01-02";
  const run = "a".repeat(32);
  const prefix = `runs/${day}/${run}`;
  const html = bytes("<!doctype html><title>Digest</title>");
  const css = bytes("body{color:black}");
  const cssHash = hash(css);
  const manifest = bytes(JSON.stringify({
    schema_version: 1, run_id: run, publication_date: day,
    artifacts: [
      { path: "index.html", sha256: hash(html), content_type: "text/html" },
      { path: `assets/${cssHash}.css`, sha256: cssHash, content_type: "text/css" },
    ],
  }));
  const pointer = bytes(JSON.stringify({ schema_version: 1, date: day, run_prefix: prefix, manifest_sha256: hash(manifest) }));
  const storage = new MemoryStorage(new Map([
    ["current.json", { data: pointer, contentType: "application/json" }],
    [`days/${day}.json`, { data: pointer, contentType: "application/json" }],
    [`${prefix}/manifest.json`, { data: manifest, contentType: "application/json" }],
    [`${prefix}/index.html`, { data: html, contentType: "text/html" }],
    [`${prefix}/assets/${cssHash}.css`, { data: css, contentType: "text/css" }],
  ]));
  const handle = createHandler(storage);
  const home = await handle(new Request("http://site/"));
  expect(home.status).toBe(200);
  expect(home.headers.get("content-security-policy")).toContain("default-src 'none'");
  expect(await home.text()).toContain("Digest");
  const asset = await handle(new Request(`http://site/assets/${day}/${run}/${cssHash}.css`));
  expect(asset.status).toBe(200);
  expect(asset.headers.get("cache-control")).toContain("immutable");
  expect((await handle(new Request("http://site/runs/secret"))).status).toBe(404);
  expect((await handle(new Request("http://site/health"))).status).toBe(200);
});
