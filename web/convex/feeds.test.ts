import { describe, expect, test } from "vitest";
import { candidateFeedUrls, feedTitle, findAlternateFeeds, looksLikeFeed } from "./feeds";

describe("candidateFeedUrls", () => {
  test("a bare Substack handle becomes its substack.com feed", () => {
    expect(candidateFeedUrls("bytebytego")).toEqual(["https://bytebytego.substack.com/feed"]);
  });

  test("a Substack URL keeps its host and points at /feed", () => {
    expect(candidateFeedUrls("https://garymarcus.substack.com/p/some-post?x=1")).toEqual([
      "https://garymarcus.substack.com/feed",
      "https://garymarcus.substack.com/p/some-post?x=1",
    ]);
  });

  test("a custom domain without a scheme gets https and /feed first", () => {
    expect(candidateFeedUrls("www.lennysnewsletter.com")).toEqual([
      "https://www.lennysnewsletter.com/feed",
      "https://www.lennysnewsletter.com/",
    ]);
  });

  test("a URL that already ends in /feed is tried as-is only", () => {
    expect(candidateFeedUrls("https://blog.bytebytego.com/feed")).toEqual([
      "https://blog.bytebytego.com/feed",
    ]);
  });

  test("whitespace, empty input and non-http schemes give nothing", () => {
    expect(candidateFeedUrls("   ")).toEqual([]);
    expect(candidateFeedUrls("javascript:alert(1)")).toEqual([]);
    expect(candidateFeedUrls("ftp://example.com")).toEqual([]);
  });

  test("http input is upgraded to https, which the digest runner requires", () => {
    expect(candidateFeedUrls("http://garymarcus.substack.com/feed")).toEqual([
      "https://garymarcus.substack.com/feed",
    ]);
  });

  test("private and local hosts are refused", () => {
    expect(candidateFeedUrls("http://localhost/feed")).toEqual([]);
    expect(candidateFeedUrls("http://127.0.0.1/feed")).toEqual([]);
    expect(candidateFeedUrls("http://10.0.0.5/feed")).toEqual([]);
    expect(candidateFeedUrls("http://169.254.169.254/latest")).toEqual([]);
  });
});

describe("looksLikeFeed", () => {
  test("RSS and Atom documents are feeds; HTML is not", () => {
    expect(looksLikeFeed('<?xml version="1.0"?><rss version="2.0"><channel>')).toBe(true);
    expect(looksLikeFeed('<feed xmlns="http://www.w3.org/2005/Atom">')).toBe(true);
    expect(looksLikeFeed("<!doctype html><html><head>")).toBe(false);
  });
});

describe("feedTitle", () => {
  test("reads the channel title, unwrapping CDATA and entities", () => {
    expect(feedTitle("<rss><channel><title><![CDATA[Marcus on AI]]></title><item><title>x")).toBe(
      "Marcus on AI",
    );
    expect(feedTitle("<feed><title>Lenny&#39;s Newsletter &amp; Podcast</title>")).toBe(
      "Lenny's Newsletter & Podcast",
    );
    expect(feedTitle("<rss><channel></channel></rss>")).toBeNull();
  });
});

describe("findAlternateFeeds", () => {
  test("finds RSS/Atom alternate links and resolves them against the page", () => {
    const html =
      '<link rel="alternate" type="application/rss+xml" href="/feed.xml">' +
      '<link type="application/atom+xml" rel="alternate" href="https://other.example/atom">' +
      '<link rel="stylesheet" href="/style.css">';
    expect(findAlternateFeeds(html, "https://example.com/blog/")).toEqual([
      "https://example.com/feed.xml",
      "https://other.example/atom",
    ]);
  });
});
