// Pure proxy-address generation. Uniqueness is enforced by the caller
// (users.upsertFromClerk retries on collision); this module has no I/O.

/** Lowercase RFC 4648 base32 alphabet (32 symbols, so `byte & 31` is unbiased). */
export const BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";
export const RANDOM_LENGTH = 8;
const SLUG_MAX = 12;

export type RandomBytes = (n: number) => Uint8Array;

const cryptoRandomBytes: RandomBytes = (n) => crypto.getRandomValues(new Uint8Array(n));

/** Slug from the email local part: lowercased, [a-z0-9] only, max 12, fallback "u". */
export function slugFromEmail(email: string): string {
  const at = email.indexOf("@");
  const local = at === -1 ? email : email.slice(0, at);
  const slug = local.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, SLUG_MAX);
  return slug || "u";
}

/** `<slug>-<8 random base32 chars>@<domain>`. */
export function generateProxyAddress(
  email: string,
  domain: string,
  randomBytes: RandomBytes = cryptoRandomBytes,
): string {
  const host = domain.trim().toLowerCase();
  if (!host) throw new Error("Proxy address domain is empty (set RESEND_INBOUND_DOMAIN)");
  const bytes = randomBytes(RANDOM_LENGTH);
  let suffix = "";
  for (let i = 0; i < RANDOM_LENGTH; i++) suffix += BASE32_ALPHABET[bytes[i] & 31];
  return `${slugFromEmail(email)}-${suffix}@${host}`;
}
