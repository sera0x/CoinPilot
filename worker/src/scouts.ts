/**
 * Scouts: hunt token leads out on the open internet, ported from
 * subagents/scout.py. One function per source; run in parallel.
 *   news   - RSS/Atom feeds from crypto outlets
 *   forums - new posts on the meme coin subreddits
 *   feeds  - DexScreener's own new-profile and boost streams
 */

import { fetchJson, latestBoosts, latestProfiles } from "./dexscreener";
import type { Config, Lead } from "./types";

// Solana mints are base58, 32-44 characters.
const MINT_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
// Cashtags are the reliable way people flag a ticker: $WIF, $BONK
const TICKER_RE = /\$([A-Za-z][A-Za-z0-9]{1,11})\b/g;

const MAX_SUMMARY = 2000;

export function extract(text: string): { mints: string[]; tickers: string[] } {
  const body = (text || "").slice(0, MAX_SUMMARY);
  const mints = [...new Set(body.match(MINT_RE) || [])].sort();
  const tickers = [...new Set((body.match(TICKER_RE) || []).map((t) => t.slice(1).toUpperCase()))]
    .filter((t) => t.length >= 2)
    .sort()
    .slice(0, 8);
  return { mints, tickers };
}

function makeLead(source: string, title: string, url: string, body = "", published: string | null = null): Lead {
  const { mints, tickers } = extract(`${title} ${body}`);
  return {
    source,
    title: (title || "").trim().slice(0, 200),
    url: (url || "").trim().slice(0, 300),
    mints,
    tickers,
    published_at: published,
  };
}

function headers(config: Config): Record<string, string> {
  const agent = (config.scan.user_agent || "").trim();
  return agent ? { "User-Agent": agent } : { "User-Agent": "Mozilla/5.0 (compatible; CoinPilot/1.0)" };
}

function tag(block: string, name: string): string {
  const match = block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`, "i"));
  if (!match) return "";
  return match[1]
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

export async function collectNews(config: Config): Promise<Lead[]> {
  const leads: Lead[] = [];
  for (const feedUrl of (config.scan.news_feeds || []).slice(0, 8)) {
    try {
      const response = await fetch(feedUrl, { headers: headers(config) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const raw = await response.text();
      const blocks = [...raw.matchAll(/<(?:item|entry)[\s\S]*?<\/(?:item|entry)>/g)].map((m) => m[0]).slice(0, 40);
      for (const block of blocks) {
        const title = tag(block, "title");
        let link = tag(block, "link");
        if (!link) {
          const href = block.match(/<link[^>]*href="([^"]+)"/i);
          link = href ? href[1] : "";
        }
        const summary = tag(block, "description") || tag(block, "summary");
        leads.push(makeLead("news", title, link, summary, tag(block, "pubDate") || tag(block, "updated")));
      }
    } catch (exc) {
      console.log(`feed ${feedUrl} failed: ${exc}`);
    }
  }
  return leads;
}

export async function collectForums(config: Config): Promise<Lead[]> {
  const leads: Lead[] = [];
  for (const subreddit of (config.scan.forum_subreddits || []).slice(0, 8)) {
    try {
      const payload = await fetchJson(
        `https://www.reddit.com/r/${subreddit}/new.json?limit=50&raw_json=1`,
        { headers: headers(config) }
      );
      const children = payload?.data?.children || [];
      for (const child of children) {
        const data = child?.data;
        if (!data) continue;
        const permalink = data.permalink || "";
        leads.push(
          makeLead(
            "forums",
            `r/${subreddit}: ${data.title || ""}`,
            permalink ? `https://www.reddit.com${permalink}` : "",
            data.selftext || "",
            data.created_utc ? String(data.created_utc) : null
          )
        );
      }
    } catch (exc) {
      console.log(`r/${subreddit} failed: ${exc}`);
    }
  }
  return leads;
}

export async function collectFeeds(config: Config): Promise<Lead[]> {
  const chain = config.chain;
  const leads: Lead[] = [];

  for (const [label, fetcher] of [
    ["new token profile", latestProfiles],
    ["boosted token", latestBoosts],
  ] as const) {
    try {
      const items = await fetcher();
      for (const item of items) {
        if (item.chainId !== chain || !item.tokenAddress) continue;
        leads.push({
          source: "feeds",
          title: `${label}: ${String(item.description || "").slice(0, 120)}`.trim(),
          url: item.url || "",
          mints: [item.tokenAddress],
          tickers: [],
          published_at: null,
        });
      }
    } catch (exc) {
      console.log(`${label} source failed: ${exc}`);
    }
  }

  for (const slug of (config.scan.metas || []).slice(0, 8)) {
    try {
      const payload = await fetchJson(`https://api.dexscreener.com/metas/meta/v1/${slug}`);
      for (const pair of Array.isArray(payload?.pairs) ? payload.pairs : []) {
        const snap = pair?.baseToken;
        if (!snap?.address) continue;
        leads.push({
          source: "feeds",
          title: `meta ${slug}: ${snap.symbol || ""}`,
          url: pair.url || "",
          mints: [snap.address],
          tickers: snap.symbol ? [String(snap.symbol).toUpperCase()] : [],
          published_at: null,
        });
      }
    } catch (exc) {
      console.log(`meta ${slug} failed: ${exc}`);
    }
  }
  return leads;
}

export const COLLECTORS: Record<string, (config: Config) => Promise<Lead[]>> = {
  news: collectNews,
  forums: collectForums,
  feeds: collectFeeds,
};
