// Pure helpers for turning what a user types (a Substack handle, a post URL, a
// custom domain) into candidate RSS/Atom feed URLs, and for reading a fetched
// document. No I/O here; subscriptions.ts does the fetching.

const PRIVATE_HOST =
  /^(localhost|.*\.localhost|.*\.local|.*\.internal|0\.0\.0\.0|\[.*\]|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|169\.254\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$/i;

/** A handle like "bytebytego": letters, digits and hyphens, no dot. */
const SUBSTACK_HANDLE = /^[a-z0-9][a-z0-9-]{0,62}$/i;

function publicHttpUrl(raw: string): URL | null {
  let url: URL;
  try {
    url = new URL(/^[a-z][a-z0-9+.-]*:/i.test(raw) ? raw : `https://${raw}`);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  if (!url.hostname.includes(".") || PRIVATE_HOST.test(url.hostname)) return null;
  return url;
}

/**
 * URLs to try, best first. Substack publications (subdomain or custom domain)
 * serve their feed at `/feed`; anything else may advertise one in its HTML, so
 * the page itself is the fallback candidate.
 */
export function candidateFeedUrls(input: string): string[] {
  const raw = input.trim();
  if (!raw) return [];
  if (SUBSTACK_HANDLE.test(raw)) return [`https://${raw.toLowerCase()}.substack.com/feed`];
  const url = publicHttpUrl(raw);
  if (url === null) return [];
  url.hash = "";
  const origin = `${url.protocol}//${url.host}`;
  if (/\/feed\/?$/.test(url.pathname)) return [url.toString()];
  const page = url.toString();
  return page === `${origin}/feed` ? [page] : [`${origin}/feed`, page];
}

/** True when the start of a document is an RSS or Atom feed. */
export function looksLikeFeed(body: string): boolean {
  const head = body.slice(0, 2000).toLowerCase();
  return head.includes("<rss") || /<feed[\s>]/.test(head);
}

const ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&apos;": "'",
};

function decode(text: string): string {
  return text
    .replace(/^<!\[CDATA\[([\s\S]*)\]\]>$/, "$1")
    .replace(/&(amp|lt|gt|quot|#39|apos);/g, (m) => ENTITIES[m])
    .trim();
}

/** The feed's own title (the first <title>, which precedes any item), or null. */
export function feedTitle(body: string): string | null {
  const m = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(body);
  const title = m ? decode(m[1].trim()) : "";
  return title || null;
}

/** RSS/Atom `<link rel="alternate">` targets in an HTML page, resolved to absolute URLs. */
export function findAlternateFeeds(html: string, pageUrl: string): string[] {
  const found: string[] = [];
  for (const tag of html.match(/<link\b[^>]*>/gi) ?? []) {
    if (!/rel\s*=\s*["']?alternate/i.test(tag)) continue;
    if (!/type\s*=\s*["']?application\/(rss|atom)\+xml/i.test(tag)) continue;
    const href = /href\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1];
    if (!href) continue;
    try {
      const resolved = new URL(href, pageUrl);
      if (publicHttpUrl(resolved.toString()) !== null) found.push(resolved.toString());
    } catch {
      // ignore malformed hrefs
    }
  }
  return found;
}

/** Re-checks a URL found inside fetched content before it is fetched in turn. */
export function isPublicHttpUrl(raw: string): boolean {
  return publicHttpUrl(raw) !== null;
}
