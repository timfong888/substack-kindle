// Pure helpers for the Kindle step: validating the user's Send-to-Kindle
// address, issuing the per-user sender address they approve on Amazon, and
// linking to the Amazon page where they approve it. No I/O.
import { BASE32_ALPHABET, type RandomBytes } from "./proxyAddress";

/** 26 base32 characters = 130 random bits. */
export const SENDER_TOKEN_LENGTH = 26;

const cryptoRandomBytes: RandomBytes = (n) => crypto.getRandomValues(new Uint8Array(n));

const KINDLE_EMAIL = /^[^\s@]+@(free\.)?kindle\.com$/i;

export function isKindleEmail(value: string): boolean {
  return KINDLE_EMAIL.test(value.trim());
}

/** Lowercased, trimmed Send-to-Kindle address, or null when it isn't one. */
export function normalizeKindleEmail(value: string): string | null {
  const email = value.trim().toLowerCase();
  return isKindleEmail(email) ? email : null;
}

/**
 * The unguessable part of a user's sender address. A spoofer needs both this
 * and the user's Kindle address, whether or not Amazon enforces DMARC.
 */
export function generateSenderToken(randomBytes: RandomBytes = cryptoRandomBytes): string {
  const bytes = randomBytes(SENDER_TOKEN_LENGTH);
  let token = "";
  for (let i = 0; i < SENDER_TOKEN_LENGTH; i++) token += BASE32_ALPHABET[bytes[i] & 31];
  return token;
}

/** `kindle+<token>@<domain>`: the address the user adds to Amazon's approved list. */
export function senderAddress(token: string, domain: string): string {
  const host = domain.trim().toLowerCase();
  if (!host) throw new Error("Sender domain is empty (set KINDLE_SENDER_DOMAIN)");
  return `kindle+${token}@${host}`;
}

/** Amazon stores with Send-to-Kindle settings. */
export const AMAZON_STORES = ["com", "co.uk", "ca", "de", "fr", "es", "it", "co.jp", "in", "com.au"];

/**
 * Manage Your Content and Devices → Preferences. The `#/home/settings/payment`
 * anchor opens the Preferences tab; it may be lost after Amazon's sign-in
 * redirect, so the UI also spells out the click path.
 */
export function amazonSettingsUrl(store: string): string {
  const tld = AMAZON_STORES.includes(store) ? store : "com";
  return `https://www.amazon.${tld}/hz/mycd/myx#/home/settings/payment`;
}
