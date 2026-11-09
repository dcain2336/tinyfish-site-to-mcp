"""TinyFish REST client — Search, Fetch, Agent (SSE), Monitor.

Endpoints and shapes verified 2026-11-08 against the public docs
(docs.tinyfish.ai) and a live no-key probe (each endpoint returns
401 MISSING_API_KEY without a key, confirming host + path exist):
  - Search:  GET  https://api.search.tinyfish.ai?query=...
  - Fetch:   POST https://api.fetch.tinyfish.ai
  - Agent:   POST https://agent.tinyfish.ai/v1/automation/run-sse
  - Monitor: POST https://agent.tinyfish.ai/v1/monitors

Auth: X-API-Key header, key from TINYFISH_API_KEY (minted free at
agent.tinyfish.ai/api-keys). Search + Fetch are $0 within the daily
allowance; Agent is $0.016/step; Monitor is $0.005/completed run.

Agent SSE event shape and the sync-run warning come from the
cookbook-derived API reference (github.com/giaphutran12/upstream
docs/research/tinyfish-api-reference.md, 2026-08-21) — NOT the official
docs. Treat them as "verify with a live key before submitting".
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
AGENT_SSE_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"
MONITOR_URL = "https://agent.tinyfish.ai/v1/monitors"

USER_AGENT = "TinyFishSiteMCP/0.1 (bounty demo; contact: arc.agent.ops@gmail.com)"


class TinyFishError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class TinyFishClient:
    """Thin client over the four TinyFish primitives this bounty tags:
    SEARCH, FETCH, AGENT, MONITOR."""

    def __init__(self, api_key=None, timeout=120):
        self.api_key = api_key or os.environ.get("TINYFISH_API_KEY")
        if not self.api_key:
            raise TinyFishError(
                "TINYFISH_API_KEY is not set. Mint a free key at "
                "https://agent.tinyfish.ai/api-keys and export it."
            )
        self.timeout = timeout

    # ------------------------------------------------------------------ http

    def _request(self, method, url, body=None, timeout=None, retries=3):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self.api_key)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", USER_AGENT)
        last_err = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise TinyFishError(f"HTTP {e.code}: {detail}", status=e.code) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise TinyFishError(f"network error: {e}") from e
        raise TinyFishError(f"request failed: {last_err}")

    # ------------------------------------------------------------------ Search

    def search(self, query, include_domains=None, language="en", page=0):
        """TinyFish Search. Returns the documented shape:
        {query, total_results, results: [{position, site_name, title,
        snippet, url}]}. Search is $0 within the daily allowance."""
        params = {"query": query, "language": language, "page": page}
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
        return self._request("GET", url)

    # ------------------------------------------------------------------ Fetch

    def fetch(self, urls, fmt="markdown", ttl=0, include_selectors=None):
        """TinyFish Fetch, up to 10 URLs per call. Returns:
        {results: [{url, final_url, title, description, language, format,
        text, links, image_links, latency_ms}],
        errors: [{url, error, status?}]}. Fetch is $0 within the allowance.
        ttl=0 forces a live fetch instead of a cached copy.
        include_selectors scopes extraction to the matching elements and
        returns them VERBATIM (docs: "automatic boilerplate removal is
        bypassed"), so classes and hrefs survive — unlike the default
        reader-mode extraction, which strips the bibrec table's class and
        all hrefs. Required for parsing Gutenberg pages through Fetch."""
        if isinstance(urls, str):
            urls = [urls]
        if len(urls) > 10:
            raise TinyFishError("Fetch takes at most 10 URLs per call")
        body = {"urls": urls, "format": fmt, "ttl": ttl}
        if include_selectors:
            body["include_selectors"] = include_selectors
        return self._request("POST", FETCH_URL, body)

    # ------------------------------------------------------------------ Agent

    def agent_stream(self, url, goal, browser_profile="lite", timeout=300):
        """TinyFish Agent over the SSE endpoint (the sync /run endpoint is
        documented as returning early with result=null — avoid it).
        Body shape and SSE event flow come from the cookbook-derived
        reference; event parsing is defensive by design. Returns the final
        result payload (string or dict)."""
        body = {"url": url, "goal": goal, "browser_profile": browser_profile}
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(AGENT_SSE_URL, data=data, method="POST")
        req.add_header("X-API-Key", self.api_key)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        req.add_header("User-Agent", USER_AGENT)
        deadline = time.time() + timeout
        final_result = None
        last_event = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                buf = b""
                while time.time() < deadline:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n\n" in buf:
                        raw, buf = buf.split(b"\n\n", 1)
                        for line in raw.decode("utf-8", "replace").splitlines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if not payload:
                                continue
                            try:
                                event = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            last_event = event
                            status = event.get("status") or event.get("type")
                            if status in ("COMPLETED", "COMPLETE"):
                                result = event.get("result")
                                if result is None:
                                    result = event.get("result_json") or event.get("resultJson")
                                if result is not None:
                                    final_result = result
                                    break
                            elif status in ("FAILED", "CANCELLED", "ERROR"):
                                raise TinyFishError(
                                    f"agent run {status}: "
                                    f"{(event.get('error') or {}) if isinstance(event.get('error'), dict) else event.get('error')}"
                                )
                        if final_result is not None:
                            break
                    if final_result is not None:
                        break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise TinyFishError(f"agent HTTP {e.code}: {detail}", status=e.code) from e
        if final_result is None:
            raise TinyFishError(
                f"agent run produced no COMPLETED result (last event: {str(last_event)[:200]})"
            )
        # Cookbook convention: COMPLETED only means the browser ran without
        # crashing — validate the content, not just the status.
        if isinstance(final_result, str) and not final_result.strip():
            raise TinyFishError("agent run COMPLETED but returned an empty result")
        return final_result

    # ------------------------------------------------------------------ Monitor

    def create_monitor(self, url, schedule_cron, name, purpose=None):
        """Create a TinyFish Page Monitor. $0.005 per completed run.
        Body shape per docs.tinyfish.ai/monitor + TinyFish Monitor guide;
        confirm against a live key before submitting."""
        body = {
            "type": "fetch",
            "config": {"url": url},
            "schedule_cron": schedule_cron,
            "name": name,
        }
        if purpose:
            body["purpose"] = purpose
        return self._request("POST", MONITOR_URL, body)
