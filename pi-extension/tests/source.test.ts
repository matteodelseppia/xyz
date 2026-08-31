import { describe, expect, test } from "bun:test";
import { isPrivate, text } from "../src/index";

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

test("normalization strips active markup and bounds output", () => {
  const normalized = text(`<style>secret</style><script>ignore()</script><p>Hello &amp; welcome</p>${"x".repeat(30_000)}`);
  expect(normalized.startsWith("Hello & welcome")).toBe(true);
  expect(normalized).not.toContain("ignore");
  expect(normalized.length).toBeLessThanOrEqual(20_000);
});
