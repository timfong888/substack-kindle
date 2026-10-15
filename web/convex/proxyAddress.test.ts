import { describe, expect, test } from "vitest";
import {
  BASE32_ALPHABET,
  RANDOM_LENGTH,
  generateProxyAddress,
  slugFromEmail,
} from "./proxyAddress";

const DOMAIN = "abc123.resend.app";

describe("slugFromEmail", () => {
  test("lowercases and keeps only [a-z0-9] from the local part", () => {
    expect(slugFromEmail("Tim.Fong+news@example.com")).toBe("timfongnews");
  });

  test("truncates to 12 characters", () => {
    expect(slugFromEmail("averyveryverylongname@example.com")).toBe("averyveryver");
  });

  test("falls back to 'u' when nothing usable remains", () => {
    expect(slugFromEmail("._-+@example.com")).toBe("u");
    expect(slugFromEmail("")).toBe("u");
    expect(slugFromEmail("@example.com")).toBe("u");
  });

  test("drops non-ASCII letters", () => {
    expect(slugFromEmail("JÖRG@example.com")).toBe("jrg");
  });
});

describe("generateProxyAddress", () => {
  test("shape is <slug>-<8 base32 chars>@<domain>", () => {
    const addr = generateProxyAddress("Tim@example.com", DOMAIN);
    expect(addr).toMatch(/^tim-[a-z2-7]{8}@abc123\.resend\.app$/);
  });

  test("uses the injected random source, mapped onto the base32 alphabet", () => {
    const bytes = [0, 1, 25, 26, 31, 32, 255, 63];
    const addr = generateProxyAddress("a@b.c", DOMAIN, (n) => {
      expect(n).toBe(RANDOM_LENGTH);
      return Uint8Array.from(bytes);
    });
    expect(addr).toBe("a-abz27a77@abc123.resend.app");
  });

  test("lowercases the domain and trims whitespace", () => {
    expect(generateProxyAddress("x@y.z", "  ABC.Resend.App ")).toMatch(/@abc\.resend\.app$/);
  });

  test("rejects an empty domain", () => {
    expect(() => generateProxyAddress("x@y.z", "")).toThrow(/domain/i);
  });

  test("default random source produces varied output", () => {
    const seen = new Set(
      Array.from({ length: 50 }, () => generateProxyAddress("x@y.z", DOMAIN)),
    );
    expect(seen.size).toBe(50);
  });

  test("alphabet is lowercase RFC 4648 base32", () => {
    expect(BASE32_ALPHABET).toBe("abcdefghijklmnopqrstuvwxyz234567");
  });
});
