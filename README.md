# Site to MCP — turn Project Gutenberg into an agent tool

TinyFish Bounty Drop 002 — **Site to MCP**: "turn a website without an
API into an agent tool." Closes **Tue Nov 17, 2026, 11:59 PM PT**.

This is an MCP server (stdio, zero dependencies, pure Python stdlib) that
exposes Project Gutenberg — a website with no official JSON API — as six
agent tools, powered by TinyFish primitives:

| MCP tool | TinyFish primitive | What it does |
|---|---|---|
| `search_books` | Search | Find books by title/author/subject, scoped to gutenberg.org |
| `get_book` | Fetch | Structured metadata for one ebook: title, author, subjects, downloads, format links |
| `get_book_text` | Fetch | Opening excerpt of the plain-text edition (header skipped) |
| `list_new_releases` | Fetch | Books recently posted or updated |
| `find_and_open` | Search → Agent | Multi-step: search, open the top book page, extract metadata as JSON (or open a given book URL directly) |
| `watch_new_releases` | Monitor | Page Monitor on the recent-postings page; baseline now, diffs on schedule |

## Why Project Gutenberg

- No official JSON REST API (the catalog is RDF/XML dumps; Gutendex is a
  third-party wrapper, not official) — a genuine "website without an API."
- Public domain, no login, no paywall — nothing to circumvent.
- Its robots.txt disallows only `/ebooks/search`. This server never fetches
  that page: search goes through TinyFish Search, exactly as the bounty
  intends. Book pages, browse pages and text editions are all allowed, and
  the server fetches politely (identifying User-Agent, 1s+ between direct
  requests in verification mode; TinyFish handles politeness server-side on
  the real path).

## Setup

```bash
# 1. Free TinyFish key (no card): https://agent.tinyfish.ai/api-keys
export TINYFISH_API_KEY=<your key>

# 2. Run — no dependencies to install (Python 3.9+ stdlib only)
python3 server.py
```

Claude Desktop / Claude Code — add to your MCP config:

```json
{"mcpServers": {"gutenberg": {
  "command": "python3",
  "args": ["/absolute/path/to/tinyfish-site-to-mcp/server.py"],
  "env": {"TINYFISH_API_KEY": "YOUR_KEY_HERE"}
}}}
```

(See `mcp_config.example.json`.)

## Costs

- Search + Fetch: **$0** within TinyFish's daily allowance (12,000
  searches + 1,000 fetches/day).
- Agent: **$0.016/step** from the wallet (student $35 credit covers it).
- Monitor: **$0.005/completed run** (~$0.15/month daily).
- This repo: $0. No paid services anywhere.

## Verification status — read this before submitting

Verified live 2026-11-08 by `python3 verify.py` (evidence in
`verification/evidence.jsonl`):

- MCP server starts, lists 6 tools, answers every tool over stdio.
- `search_books` finds "Pride and Prejudice" live; `get_book` returns real
  metadata for #1342 (Jane Austen, 224,030 downloads in the last 30 days);
  `get_book_text` returns the real opening ("It is a truth universally
  acknowledged…"); `list_new_releases` returns live postings;
  `find_and_open` resolves a Sherlock Holmes book; `watch_new_releases`
  captures a baseline and diffs against it.
- Server refuses to start without a key (exit 2) unless
  `SITE2MCP_VERIFY=1` is set.

**Honest limits — these are real, and they are the pre-submission steps:**

1. **The TinyFish REST path is wired, not yet live-tested.** The client
   follows the public docs (endpoints, `X-API-Key` auth, request/response
   shapes — all confirmed by live no-key probes returning 401
   MISSING_API_KEY). But no TinyFish API key was available while building,
   so live TinyFish calls are untested. The verification above ran through
   `direct_client.py`, a clearly-labeled stand-in with the same interface
   (the server only loads it when `SITE2MCP_VERIFY=1` is set explicitly).
   **Close it:** set `TINYFISH_API_KEY` and re-run
   `python3 verify.py --mode tinyfish` — the harness runs the same checks
   against the real API with zero code changes (it creates ONE real Page
   Monitor and prints its id so it can be deleted from the dashboard).
2. **The Agent SSE event shape and Monitor request body come from the
   cookbook-derived API reference, not the official docs.** Parsing is
   defensive, but confirm both with a live key run before submitting.
3. **The per-bounty acceptance checklist is gated.** It lives inside the
   signed-in TinyBounties dashboard. A browser agent with the TinyFish
   session must read the Site to MCP bounty page and check this build
   against it before submission. Do not submit until that read is done.

## Pre-submission checklist

- [ ] Set `TINYFISH_API_KEY`, run `python3 verify.py --mode tinyfish`,
      confirm all tools pass against TinyFish (not the stand-in). Note the
      real monitor id it prints; delete it from the dashboard if unwanted.
- [ ] Signed-in read of the Site to MCP bounty page on tinybounties.com;
      confirm every acceptance criterion is met.
- [ ] Claim the bounty on tinybounties.com.
- [ ] Push this repo to GitHub (public).
- [ ] Post on X and LinkedIn + Discord #showcase (see X draft in the
      submission notes; Scott posts — one-item X permission granted Nov 8).
- [ ] Submit repo + post links on the tinybounties.com dashboard.

## Files

| File | What it is |
|---|---|
| `server.py` | MCP server (stdio JSON-RPC, zero deps) |
| `tinyfish_client.py` | TinyFish REST client (Search/Fetch/Agent/Monitor) |
| `gutenberg.py` | Site adapter: URLs, robots check, page parsers |
| `direct_client.py` | Verification-only stand-in (same interface, test-only) |
| `verify.py` | Live verification harness → `verification/evidence.jsonl` |
| `SPEC.md` | Spec verification record (sources, gated items) |
| `mcp_config.example.json` | Example MCP client config |
| `requirements.txt` | None — stdlib only |

## Notes

- The Agent tool follows the cookbook escalation ladder (search → fetch →
  agent) and the "one agent call per site" convention; the goal embeds the
  JSON shape it must return.
- COMPLETED only means the browser ran without crashing — the server
  validates that the Agent result actually contains content before
  returning it.
- No credentials are stored in this repo. The API key lives only in the
  environment. `.gitignore` excludes `.env` and verification state.
