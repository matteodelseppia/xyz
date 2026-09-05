import { describe, expect, test } from "bun:test";
import { isPrivate, normalizePublicationDate, pageEntries, publicationDate, text, unresolvedEvidenceIds } from "../src/index";

describe("source network policy", () => {
  test.each(["127.0.0.1", "10.2.3.4", "172.16.0.1", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"])(
    "rejects private or metadata address %s",
    address => expect(isPrivate(address)).toBe(true),
  );
  test.each(["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])(
    "permits public address %s",
    address => expect(isPrivate(address)).toBe(false),
  );
});

test("feed publication dates are normalized before entering the evidence ledger", () => {
  expect(normalizePublicationDate("Mon, 01 Sep 2026 12:00:00 GMT")).toBe("2026-09-01T12:00:00.000Z");
  expect(normalizePublicationDate("not a date")).toBeNull();
  expect(normalizePublicationDate({ date: "2026-09-01" })).toBeNull();
});

test("publication evidence validation rejects only unknown IDs", () => {
  const updates = [
    { evidence_ids: ["ev_0123456789abcdef", "ev_ffffffffffffffff"] },
    { evidence_ids: ["ev_ffffffffffffffff"] },
  ];
  expect(unresolvedEvidenceIds(updates, new Set(["ev_0123456789abcdef"]))).toEqual([
    "ev_ffffffffffffffff",
  ]);
});

test("normalization strips active markup and bounds output", () => {
  const normalized = text(`<style>secret</style><script>ignore()</script><p>Hello &amp; welcome</p>${"x".repeat(30_000)}`);
  expect(normalized.startsWith("Hello & welcome")).toBe(true);
  expect(normalized).not.toContain("ignore");
  expect(normalized.length).toBeLessThanOrEqual(20_000);
});

test("news-page fallback returns same-origin article references and publication dates", () => {
  const entries = pageEntries(
    '<a href="/article">Useful agent update</a><a href="https://other.example/article">Ignore this</a>',
    { id: "news", name: "News", description: "News source", feed_url: "https://example.com/news" },
    "https://example.com/news",
  );
  expect(entries).toHaveLength(1);
  expect(entries[0]?.url).toBe("https://example.com/article");
  expect(publicationDate('<meta property="article:published_time" content="2026-09-01T12:00:00Z">')).toBe("2026-09-01T12:00:00.000Z");
  expect(publicationDate("<p>Published on September 1, 2026</p>")).toBe("2026-09-01T00:00:00.000Z");
});
