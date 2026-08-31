import { createHash, randomBytes } from "node:crypto";
import { promises as dns } from "node:dns";
import { isIP } from "node:net";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { XMLParser } from "fast-xml-parser";

type Source = { id: string; name: string; description: string; feed_url: string };
type Registry = { version: number; sources: Source[] };
type Ref = { sourceId: string; url: string; title: string; text: string };
type Evidence = {
  id: string; source_id: string; url: string; title: string;
  published_at: string | null; text: string; kind: "feed" | "entry" | "link";
};

const MAX_RESPONSE = 1_000_000;
const MAX_TEXT = 20_000;
const TIMEOUT_MS = 12_000;
const registry = JSON.parse(process.env.XYZ_SOURCE_REGISTRY ?? '{"version":1,"sources":[]}') as Registry;
const role = process.env.XYZ_PI_ROLE ?? "producer";
const maxBudget = Number(process.env.XYZ_TOOL_BUDGET ?? "24");
const allowPrivate = process.env.XYZ_ALLOW_PRIVATE_SOURCES === "1";
let calls = 0;
let active = 0;
const refs = new Map<string, Ref>();

export function text(value: unknown): string {
  return String(value ?? "")
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&(?:nbsp|#160);/gi, " ")
    .replace(/&amp;/gi, "&").replace(/&lt;/gi, "<").replace(/&gt;/gi, ">")
    .replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);
}

function token(value: Ref): string {
  const ref = `ref_${randomBytes(12).toString("hex")}`;
  refs.set(ref, value);
  return ref;
}

function evidence(value: Ref, kind: Evidence["kind"], publishedAt: string | null = null): Evidence {
  const clean = text(value.text);
  const id = `ev_${createHash("sha256").update(`${value.sourceId}\0${value.url}\0${clean}`).digest("hex").slice(0, 16)}`;
  return { id, source_id: value.sourceId, url: value.url, title: text(value.title).slice(0, 300), published_at: publishedAt, text: clean, kind };
}

export function isPrivate(address: string): boolean {
  if (isIP(address) === 4) {
    const parts = address.split(".").map(Number);
    const [a = 0, b = 0] = parts;
    return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || a >= 224;
  }
  const ip = address.toLowerCase();
  return ip === "::" || ip === "::1" || ip.startsWith("fe8") || ip.startsWith("fe9") ||
    ip.startsWith("fea") || ip.startsWith("feb") || ip.startsWith("fc") || ip.startsWith("fd") ||
    ip.startsWith("::ffff:127.") || ip.startsWith("::ffff:10.") || ip.startsWith("::ffff:192.168.");
}

async function checkUrl(raw: string): Promise<URL> {
  const url = new URL(raw);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error("URL is not permitted");
  const addresses = await dns.lookup(url.hostname, { all: true, verbatim: true });
  if (!addresses.length || (!allowPrivate && addresses.some(item => isPrivate(item.address)))) {
    throw new Error("destination network is not permitted");
  }
  return url;
}

async function boundedFetch(raw: string): Promise<{ body: string; url: string; contentType: string }> {
  if (++calls > maxBudget) throw new Error("source tool budget exhausted");
  if (++active > 3) { active--; throw new Error("source tool concurrency limit exceeded"); }
  try {
    let current = raw;
    for (let redirects = 0; redirects <= 3; redirects++) {
      const url = await checkUrl(current);
      const response = await fetch(url, {
        redirect: "manual",
        signal: AbortSignal.timeout(TIMEOUT_MS),
        headers: { "user-agent": "matteodelseppia.xyz/1.0", accept: "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.8" },
      });
      if (response.status >= 300 && response.status < 400) {
        const location = response.headers.get("location");
        if (!location || redirects === 3) throw new Error("redirect limit exceeded");
        current = new URL(location, url).toString();
        continue;
      }
      if (!response.ok) throw new Error(`source returned HTTP ${response.status}`);
      const declared = Number(response.headers.get("content-length") ?? "0");
      if (declared > MAX_RESPONSE) throw new Error("source response is too large");
      const reader = response.body?.getReader();
      if (!reader) throw new Error("source response has no body");
      const chunks: Uint8Array[] = [];
      let size = 0;
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        size += result.value.byteLength;
        if (size > MAX_RESPONSE) { await reader.cancel(); throw new Error("source response is too large"); }
        chunks.push(result.value);
      }
      const bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      return { body: new TextDecoder().decode(bytes), url: response.url || current, contentType: response.headers.get("content-type") ?? "" };
    }
    throw new Error("redirect limit exceeded");
  } finally { active--; }
}

function array<T>(value: T | T[] | undefined): T[] { return value === undefined ? [] : Array.isArray(value) ? value : [value]; }
function href(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const item = value as Record<string, unknown>;
    return String(item.href ?? item["@_href"] ?? item["#text"] ?? "");
  }
  return "";
}

const Finding = Type.Object({
  category: Type.Union([
    Type.Literal("factual_support"), Type.Literal("attribution"), Type.Literal("originality"),
    Type.Literal("relevance"), Type.Literal("duplication"), Type.Literal("readability"),
    Type.Literal("brevity"), Type.Literal("security"), Type.Literal("integrity"),
  ]),
  affected_content: Type.String({ minLength: 1, maxLength: 160 }),
  correction: Type.String({ minLength: 1, maxLength: 360 }),
  rule: Type.Optional(Type.String({ maxLength: 100 })),
}, { additionalProperties: false });

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "list_sources", label: "List sources", description: "List configured feed IDs and descriptions. Takes no URLs.",
    parameters: Type.Object({}, { additionalProperties: false }),
    async execute() {
      return { content: [{ type: "text" as const, text: JSON.stringify(registry.sources.map(({ id, name, description }) => ({ id, name, description }))) }], details: { sourceCount: registry.sources.length } };
    },
  });
  pi.registerTool({
    name: "read_feed", label: "Read feed", description: "Read up to 15 recent entries from one configured source.",
    parameters: Type.Object({ source_id: Type.String() }, { additionalProperties: false }),
    async execute(_id, params) {
      const source = registry.sources.find(item => item.id === params.source_id);
      if (!source) throw new Error("unknown source_id");
      const fetched = await boundedFetch(source.feed_url);
      if (/<!DOCTYPE/i.test(fetched.body)) throw new Error("feed DOCTYPE declarations are not permitted");
      const parsed = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: "@_" }).parse(fetched.body) as Record<string, any>;
      const channel = parsed.rss?.channel ?? parsed.feed ?? parsed["rdf:RDF"] ?? {};
      const rawItems = array<any>(channel.item ?? channel.entry).slice(0, 15);
      const entries = rawItems.map(item => {
        const url = href(item.link) || href(item.guid);
        if (!url) return null;
        const absolute = new URL(url, fetched.url).toString();
        const title = text(item.title);
        const description = text(item.description ?? item.summary ?? item.content ?? item["content:encoded"]);
        const value: Ref = { sourceId: source.id, url: absolute, title, text: description || title };
        const entryRef = token(value);
        const ev = evidence(value, "feed", item.pubDate ?? item.published ?? item.updated ?? null);
        return { entry_ref: entryRef, evidence_id: ev.id, title: value.title, url: absolute, published_at: ev.published_at, description: value.text.slice(0, 1200), _evidence: ev };
      }).filter(Boolean) as Array<Record<string, unknown>>;
      const ledger = entries.map(item => item._evidence as Evidence);
      const visible = entries.map(({ _evidence, ...item }) => item);
      return { content: [{ type: "text" as const, text: JSON.stringify(visible) }], details: { evidence: ledger } };
    },
  });
  pi.registerTool({
    name: "read_entry", label: "Read entry", description: "Read normalized content from an entry_ref returned by read_feed.",
    parameters: Type.Object({ entry_ref: Type.String() }, { additionalProperties: false }),
    async execute(_id, params) {
      const ref = refs.get(params.entry_ref); if (!ref) throw new Error("unknown or expired entry_ref");
      const fetched = await boundedFetch(ref.url);
      const normalized = text(fetched.body);
      const value = { ...ref, url: fetched.url, text: normalized || ref.text };
      const ev = evidence(value, "entry");
      const links: Array<{ link_ref: string; label: string }> = [];
      for (const match of fetched.body.matchAll(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi)) {
        if (links.length >= 12) break;
        try {
          const url = new URL(match[1] ?? "", fetched.url).toString();
          if (!['http:', 'https:'].includes(new URL(url).protocol)) continue;
          links.push({ link_ref: token({ sourceId: ref.sourceId, url, title: text(match[2]), text: "linked page" }), label: text(match[2]).slice(0, 120) });
        } catch { /* malformed links are ignored */ }
      }
      return { content: [{ type: "text" as const, text: JSON.stringify({ evidence_id: ev.id, title: ev.title, url: ev.url, text: ev.text, links }) }], details: { evidence: ev } };
    },
  });
  pi.registerTool({
    name: "read_link", label: "Read linked page", description: "Read a page through a link_ref returned by read_entry.",
    parameters: Type.Object({ link_ref: Type.String() }, { additionalProperties: false }),
    async execute(_id, params) {
      const ref = refs.get(params.link_ref); if (!ref) throw new Error("unknown or expired link_ref");
      const fetched = await boundedFetch(ref.url);
      const ev = evidence({ ...ref, url: fetched.url, text: text(fetched.body) }, "link");
      return { content: [{ type: "text" as const, text: JSON.stringify({ evidence_id: ev.id, title: ev.title, url: ev.url, text: ev.text }) }], details: { evidence: ev } };
    },
  });

  if (role === "producer") pi.registerTool({
    name: "submit_publication", label: "Submit publication", description: "Submit the final typed publication and terminate production.",
    parameters: Type.Object({
      schema_version: Type.Literal(1), candidate_id: Type.String({ minLength: 6, maxLength: 80, pattern: "^[a-zA-Z0-9_-]+$" }),
      publication_date: Type.String({ format: "date" }), title: Type.String({ minLength: 1, maxLength: 100 }),
      introduction: Type.String({ minLength: 1, maxLength: 420 }),
      updates: Type.Array(Type.Object({ heading: Type.String({ minLength: 1, maxLength: 100 }), description: Type.String({ minLength: 1, maxLength: 360 }), why_read: Type.String({ minLength: 1, maxLength: 220 }), evidence_ids: Type.Array(Type.String(), { minItems: 1, maxItems: 5 }) }, { additionalProperties: false }), { maxItems: 12 }),
      revision_notes: Type.Optional(Type.Union([Type.String({ maxLength: 500 }), Type.Null()])),
    }, { additionalProperties: false }),
    async execute(_id, params) { return { content: [{ type: "text" as const, text: `Submitted ${params.candidate_id}` }], details: { artifact: params }, terminate: true }; },
  });

  if (role === "reviewer") pi.registerTool({
    name: "submit_review", label: "Submit review", description: "Submit the final typed review verdict and terminate review.",
    parameters: Type.Object({ schema_version: Type.Literal(1), candidate_id: Type.String(), approved: Type.Boolean(), findings: Type.Array(Finding, { maxItems: 12 }), rationale: Type.String({ minLength: 1, maxLength: 500 }) }, { additionalProperties: false }),
    async execute(_id, params) {
      if (params.approved && params.findings.length) throw new Error("approved reviews cannot contain findings");
      return { content: [{ type: "text" as const, text: `Review submitted: ${params.approved ? "approved" : "revision requested"}` }], details: { review: params }, terminate: true };
    },
  });
}
