"""Verification-only stand-in for TinyFishClient.

Implements the SAME method names and response shapes as TinyFishClient,
but reads the live web directly instead of through TinyFish. This exists
solely so the MCP server's tool logic can be verified live before a
TinyFish API key is available. It is NOT a second transport and must never
be presented as TinyFish usage.

Rules enforced here (mirroring what TinyFish does server-side):
  - robots.txt is honored: /ebooks/search is refused outright.
  - Polite access: identifying User-Agent, 1s minimum between requests.
  - Search goes through a search engine (DuckDuckGo Lite), never by
    scraping the site's own search page.

The server only selects this client when SITE2MCP_VERIFY=1 is set
explicitly. With a real TINYFISH_API_KEY present, TinyFishClient is used
and this module is never loaded.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from gutenberg import (
    BASE, USER_AGENT, is_disallowed, parse_robots_txt,
)

DDG_LITE = "https://html.duckduckgo.com/html/"


class DirectClient:
    mode = "direct-verification"

    def __init__(self, state_dir=None, delay=1.0):
        self.delay = delay
        self._last_fetch = 0.0
        self._robots_cache = None  # cached disallow set for gutenberg.org
        self.state_dir = state_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "verification")
        os.makedirs(self.state_dir, exist_ok=True)

    # ------------------------------------------------------------------ http

    def _polite_wait(self):
        wait = self.delay - (time.time() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch = time.time()

    def _get(self, url, timeout=30):
        self._polite_wait()
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", USER_AGENT)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _robots(self):
        if self._robots_cache is None:
            try:
                text = self._get(BASE + "/robots.txt").decode("utf-8", "replace")
                self._robots_cache = parse_robots_txt(text)
            except Exception:
                self._robots_cache = set()
        return self._robots_cache

    def _check_allowed(self, url):
        path = urllib.parse.urlparse(url).path
        if is_disallowed(path, self._robots()):
            raise ValueError(
                f"refusing {path}: disallowed by {BASE}/robots.txt — "
                "use search() (TinyFish Search) instead of fetching the "
                "site's own search page"
            )

    # ------------------------------------------------------------------ Search

    def search(self, query, include_domains=None, language="en", page=0):
        """DuckDuckGo Lite, scoped to gutenberg.org. Returns the TinyFish
        Search shape: {query, total_results, results: [{position,
        site_name, title, snippet, url}]}."""
        q = query
        if include_domains:
            q = " ".join(f"site:{d}" for d in include_domains) + " " + q
        url = DDG_LITE + "?" + urllib.parse.urlencode({"q": q})
        try:
            html = self._get(url).decode("utf-8", "replace")
        except Exception as e:
            return {"query": query, "total_results": 0, "results": [],
                    "error": f"verification search failed: {e}"}
        low = html.lower()
        if "anomaly" in low or "captcha" in low:
            # DDG serves a bot-mitigation page (HTTP 202) to datacenter IPs.
            return {"query": query, "total_results": 0, "results": [],
                    "error": "verification search engine bot-gated "
                             "(DuckDuckGo Lite anomaly page)"}
        results = []
        for m in re.finditer(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
            href = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            # DDG lite wraps outbound links as //duckduckgo.com/l/?uddg=<urlencoded>
            uddg = re.search(r"[?&]uddg=([^&]+)", href)
            if uddg:
                href = urllib.parse.unquote(uddg.group(1))
            if "gutenberg.org/ebooks/" not in href:
                continue
            results.append({
                "position": len(results) + 1,
                "site_name": "gutenberg.org",
                "title": title,
                "snippet": "",
                "url": href,
            })
            if len(results) >= 10:
                break
        return {"query": query, "total_results": len(results),
                "results": results}

    # ------------------------------------------------------------------ Fetch

    def fetch(self, urls, fmt="markdown", ttl=0, include_selectors=None):
        """Direct fetch of public pages. Returns the TinyFish Fetch shape:
        {results: [{url, final_url, title, description, language, format,
        text, links, image_links, latency_ms}], errors: [{url, error}]}.
        fmt is accepted for interface parity; text is returned as-is
        (markdown conversion is a TinyFish server-side step).
        include_selectors is accepted for interface parity and ignored:
        the direct fetch already returns the full raw page, classes and
        hrefs intact, so scoping is a no-op here."""
        if isinstance(urls, str):
            urls = [urls]
        results, errors = [], []
        for url in urls[:10]:
            try:
                self._check_allowed(url)
                t0 = time.time()
                raw = self._get(url)
                text = raw.decode("utf-8", "replace")
                title = ""
                m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
                if m:
                    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
                results.append({
                    "url": url, "final_url": url, "title": title,
                    "description": "", "language": "en", "format": fmt,
                    "text": text, "links": [], "image_links": [],
                    "latency_ms": int((time.time() - t0) * 1000),
                })
            except Exception as e:
                errors.append({"url": url, "error": str(e)[:200]})
        return {"results": results, "errors": errors}

    # ------------------------------------------------------------------ Agent

    def agent_stream(self, url, goal, browser_profile="lite", timeout=300):
        """Local composition of the same steps the TinyFish Agent would run:
        fetch the page, extract its key facts. Returns a dict (the TinyFish
        path may return a JSON string instead — both are accepted)."""
        fetched = self.fetch([url], fmt="html")
        if not fetched["results"]:
            raise ValueError(f"agent verification fetch failed: {fetched['errors']}")
        from gutenberg import parse_book_page
        page = parse_book_page(fetched["results"][0]["text"], page_url=url)
        return {
            "title": page["title"],
            "author": page["author"],
            "subjects": page["subjects"],
            "download_links": page["download_links"],
            "source_url": url,
            "goal": goal,
        }

    # ------------------------------------------------------------------ Monitor

    def create_monitor(self, url, schedule_cron, name, purpose=None):
        """One-shot baseline/diff against a local state file. Same role as a
        TinyFish Page Monitor (baseline captured on first run, diff after),
        without the server-side schedule. Returns a monitor-shaped dict."""
        key = hashlib.sha256(url.encode()).hexdigest()[:12]
        state_path = os.path.join(self.state_dir, f"monitor-{key}.json")
        fetched = self.fetch([url], fmt="html")
        if not fetched["results"]:
            raise ValueError(f"monitor verification fetch failed: {fetched['errors']}")
        from gutenberg import parse_recent_page
        current = parse_recent_page(fetched["results"][0]["text"])
        current_ids = [item["ebook_no"] for item in current]
        if not os.path.exists(state_path):
            with open(state_path, "w") as f:
                json.dump({"url": url, "ebook_nos": current_ids}, f, indent=2)
            return {"monitor_id": f"local:{key}", "name": name,
                    "type": "fetch", "config": {"url": url},
                    "schedule_cron": schedule_cron,
                    "outcome": "baseline captured",
                    "baseline_items": len(current_ids)}
        with open(state_path) as f:
            previous = json.load(f).get("ebook_nos", [])
        new_ids = [n for n in current_ids if n not in previous]
        with open(state_path, "w") as f:
            json.dump({"url": url, "ebook_nos": current_ids}, f, indent=2)
        return {"monitor_id": f"local:{key}", "name": name,
                "type": "fetch", "config": {"url": url},
                "schedule_cron": schedule_cron,
                "outcome": "changed" if new_ids else "no changes",
                "new_ebook_nos": new_ids,
                "total_items": len(current_ids)}
