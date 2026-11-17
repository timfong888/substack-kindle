import { describe, expect, test } from "vitest";
import {
  AMAZON_STORES,
  amazonSettingsUrl,
  generateSenderToken,
  isKindleEmail,
  normalizeKindleEmail,
  SENDER_TOKEN_LENGTH,
  senderAddress,
} from "./kindle";

describe("isKindleEmail / normalizeKindleEmail", () => {
  test("accepts kindle.com and free.kindle.com addresses, case-insensitively", () => {
    expect(isKindleEmail("reader_42@kindle.com")).toBe(true);
    expect(isKindleEmail("Reader@Free.Kindle.com")).toBe(true);
    expect(normalizeKindleEmail("  Reader@Kindle.COM ")).toBe("reader@kindle.com");
  });

  test("rejects other domains, look-alikes and malformed input", () => {
    expect(isKindleEmail("reader@gmail.com")).toBe(false);
    expect(isKindleEmail("reader@kindle.com.evil.example")).toBe(false);
    expect(isKindleEmail("reader@notkindle.com")).toBe(false);
    expect(isKindleEmail("@kindle.com")).toBe(false);
    expect(isKindleEmail("a b@kindle.com")).toBe(false);
    expect(normalizeKindleEmail("reader@gmail.com")).toBeNull();
  });
});

describe("generateSenderToken", () => {
  test("is long, lowercase base32 and drawn from the random source", () => {
    const token = generateSenderToken((n) => new Uint8Array(n).fill(1));
    expect(token).toHaveLength(SENDER_TOKEN_LENGTH);
    expect(token).toMatch(/^[a-z2-7]+$/);
    expect(SENDER_TOKEN_LENGTH * 5).toBeGreaterThanOrEqual(128); // >= 128 bits
  });

  test("differs between calls with real randomness", () => {
    expect(generateSenderToken()).not.toBe(generateSenderToken());
  });
});

describe("senderAddress", () => {
  test("is kindle+<token>@<domain>, lowercased", () => {
    expect(senderAddress("abc234", "Mail.Fong888.com")).toBe("kindle+abc234@mail.fong888.com");
  });

  test("refuses an empty domain", () => {
    expect(() => senderAddress("abc", " ")).toThrow(/domain/);
  });
});

describe("amazonSettingsUrl", () => {
  test("points at Manage Your Content and Devices for the chosen store", () => {
    expect(amazonSettingsUrl("com")).toBe("https://www.amazon.com/hz/mycd/myx#/home/settings/payment");
    expect(amazonSettingsUrl("co.uk")).toBe(
      "https://www.amazon.co.uk/hz/mycd/myx#/home/settings/payment",
    );
    expect(AMAZON_STORES).toContain("de");
  });
});
