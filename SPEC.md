# Site to MCP — SPEC VERIFICATION (2026-11-08)

## Verdict: PROCEED — enough of the spec is verifiable publicly

The bounty is real and the brief is unambiguous. The per-bounty acceptance
checklist is gated behind a signed-in TinyBounties dashboard read (flagged
below as a pre-submission step), but the concept, the required primitives,
the endpoints, the deadline and the submission mechanics are all public.
Nothing in this build guesses at them.

## What was verified live (2026-11-08, all by this builder)

| Spec item | Source | Result |
|---|---|---|
| Bounty exists: "Site to MCP — turn a website without an API into an agent tool" | Official TinyFish Instagram graphic, posted Nov 2, 2026 (instagram.com/p/DfPshRommMz) | Confirmed; tagged capabilities AGENT, FETCH, MONITOR |
| Bounty Drop 002, 5 bounties, closes Nov 17 | Official TinyFish LinkedIn post ("5 new bounties… Submissions close Nov 17!") | Confirmed |
| Submission flow: claim on tinybounties.com → build → post on X AND LinkedIn → submit links on dashboard → team reviews, marks approved/rejected with reason | tinyfish.ai/students FAQ (live page) | Confirmed |
| Reward: agentic app = $50 gift card + 50 points; top 3 builds per bounty win gift cards + points | tinyfish.ai/students FAQ; LinkedIn post | Confirmed |
| Four primitives: Search, Fetch, Agent, Browser (+ Monitor) | docs.tinyfish.ai (live) | Confirmed |
| Search endpoint `GET https://api.search.tinyfish.ai` | docs + live no-key probe → 401 MISSING_API_KEY | Host + path exist |
| Fetch endpoint `POST https://api.fetch.tinyfish.ai` | docs + live no-key probe → 401 MISSING_API_KEY, body `{"error":{"code":"MISSING_API_KEY",...}}` | Host + path exist |
| Agent SSE `POST https://agent.tinyfish.ai/v1/automation/run-sse` | Cookbook-derived reference (see provenance note) | Path per reference; needs key to confirm |
| Monitor `POST https://agent.tinyfish.ai/v1/monitors` | docs + live no-key probe → 401 | Host + path exist on agent.tinyfish.ai (the tinyfish.chat blog shows the same path on agent.tinyfish.chat; the .chat host did not answer from this network — the .ai host is used) |
| Auth: `X-API-Key` header, key from agent.tinyfish.ai/api-keys, env `TINYFISH_API_KEY` | docs.tinyfish.ai | Confirmed |
| Pricing: Search + Fetch $0 in daily allowance; Agent $0.016/step; Monitor $0.005/run | docs.tinyfish.ai | Confirmed |
| Target site has no official JSON API | gutenberg.org offers the catalog as RDF/XML dumps; Gutendex is third-party, not official | Confirmed (see README) |
| gutenberg.org robots.txt allows book/browse pages; disallows `/ebooks/search` only | live fetch of robots.txt | Confirmed — search goes through TinyFish Search, never a fetch of the search page |
| Book page, recent-postings page, plain-text edition all fetchable live | live curl, 2026-11-08 | Confirmed (see verification/evidence.jsonl) |

## Provenance notes (what is official vs derived)

- **Official docs** (docs.tinyfish.ai): Search/Fetch/Monitor endpoints, auth,
  pricing, MCP tool list. Highest confidence.
- **Cookbook-derived reference** (github.com/giaphutran12/upstream,
  docs/research/tinyfish-api-reference.md, research output 2026-08-21 citing
  tinyfish-cookbook files): Agent SSE request body (`{url, goal,
  browser_profile}`), SSE event flow (STARTED → PROGRESS → COMPLETE),
  "never use sync /run" warning, "COMPLETED only means the browser ran
  without crashing" convention. The client code follows it, but the event
  parsing is defensive and the README flags it as "verify with a live key".
- **Instagram graphic + LinkedIn post**: the bounty list itself. The graphic
  is the only public source naming the five Drop 002 bounties.

## Live-key verification (2026-11-08, with Scott's key)

First live-key run: **6 passed, 2 failed** — both failures in the TinyFish
Fetch path (the stand-in path passed both, so the Fetch response shape was
the suspect). Diagnosis, confirmed by probing Fetch directly:

- TinyFish Fetch's **default extraction is a reader-mode cleanup**: it
  strips element `class` attributes and **all `href`s**. The book page came
  back with the bibrec table present but classless (`<table>` instead of
  `<table class="bibrec">`) and the download section as bare format labels
  with no links; the recent-postings page came back as bare `<li>` items
  with no class and no `/ebooks/<n>` hrefs. The parsers in gutenberg.py
  key on `table.bibrec`, `li.pgdbetext`, and `a[href]` — so they saw
  nothing: `title='' author='' links=0`, `count=0`.
- **Fix (docs.tinyfish.ai/fetch, "CSS Selector Scoping"):** `include_selectors`
  returns matched elements **verbatim** — "automatic boilerplate removal is
  bypassed" — classes and hrefs intact. `get_book` now fetches with
  `include_selectors=["table.bibrec", "a.other-format-link"]` and
  `list_new_releases` with `["li.pgdbetext"]`. No parser changes were
  needed: the scoped text parses exactly (book: title/author/downloads/5
  links; recent: 149 items). `TinyFishClient.fetch()` gained the
  `include_selectors` parameter; `DirectClient.fetch()` accepts and ignores
  it (raw fetch already returns the full page).
- Re-run after the fix: **tinyfish mode 8 passed, 0 degraded, 0 failed**;
  stand-in mode 9 passed, 1 degraded (known sandbox DDG bot-gate, logic
  proven by unit check), 0 failed. Evidence: verification/evidence.jsonl.
- Note: the stand-in-mode run also exposed a harness quirk — a stale local
  monitor-baseline state file made the first watch call report `changed`
  instead of `baseline captured`. That is correct monitor behavior against
  a stale baseline, not a code bug; resetting the scratch state file
  restored the expected baseline→diff sequence. The TinyFish-mode run is
  unaffected (it creates a real server-side monitor each time).
- This fix run created one real monitor
  (`a4ba4d95-c458-455b-8baa-d7323c1f8aec`, daily 09:00 UTC) in addition to
  the earlier `e8a6c4a3-3709-41a4-9436-63c53eaf2989`. Neither was deleted;
  monitor cleanup is the parent's call.

## What is GATED (not verifiable without a sign-in)

1. **Per-bounty acceptance checklist** for Site to MCP — lives inside the
   signed-in TinyBounties dashboard (tinybounties.com is a JS + Clerk-auth
   shell; /bounties, /dashboard, /challenges, /bounty all 404
   unauthenticated; data loads from Convex, function names not enumerable).
   **Needed before submitting:** a browser agent with Scott's TinyFish
   session reads the Site to MCP bounty page and checks this build against
   its checklist. Do not submit until that read is done.
2. **Live TinyFish API calls** — verified live 2026-11-08 with Scott's key
   (pasted by him in chat; used only as env var `TINYFISH_API_KEY`, never
   stored). Tinyfish mode: 8/8 checks pass. The Agent SSE shape and Monitor
   request body, previously cookbook-derived only, are now confirmed live
   (Agent returned a real result for #1661; Monitor creation returned a
   real id).

## Eligibility

Student Program: any currently enrolled student, anywhere; $35 wallet
credit automatic with an eligible academic email (.edu etc.). Scott is a
GCU student; the $35 credit is already in his account per his record.
No business-registration, geography, or age gate is stated for the bounty
itself. The build uses no EOD/military content.
