#!/usr/bin/env python3
"""Site to MCP — an MCP server that turns Project Gutenberg (a website with
no public API) into agent tools, powered by TinyFish primitives.

Transport: MCP over stdio (JSON-RPC 2.0, protocol 2024-11-05), zero
dependencies — pure Python stdlib, so any MCP client can run it.

Client selection:
  - TINYFISH_API_KEY set  -> TinyFishClient (Search/Fetch/Agent/Monitor).
  - SITE2MCP_VERIFY=1     -> DirectClient, verification-only stand-in.
  - Neither               -> exit 2 with setup instructions.

Tools:
  search_books       TinyFish Search scoped to gutenberg.org
  get_book           TinyFish Fetch of a book page -> structured metadata
  get_book_text      TinyFish Fetch of the plain-text edition -> opening excerpt
  list_new_releases  TinyFish Fetch of the recent-postings page
  find_and_open      TinyFish Agent: search, open top result, extract as JSON
  watch_new_releases TinyFish Monitor on the recent-postings page

All stdout traffic is JSON-RPC. Logs go to stderr.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gutenberg
from gutenberg import (
    book_url, ebook_no_from_url, extract_text_excerpt,
    parse_book_page, parse_recent_page, recent_url, text_url,
)

SERVER_NAME = "gutenberg-site-to-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def make_client():
    api_key = os.environ.get("TINYFISH_API_KEY")
    if api_key:
        from tinyfish_client import TinyFishClient
        log(f"[{SERVER_NAME}] using TinyFishClient")
        return TinyFishClient(api_key)
    if os.environ.get("SITE2MCP_VERIFY") == "1":
        from direct_client import DirectClient
        log(f"[{SERVER_NAME}] WARNING: verification mode — DirectClient "
            "stand-in, not TinyFish. Set TINYFISH_API_KEY for the real path.")
        return DirectClient()
    sys.stderr.write(
        "TinyFish Site to MCP needs a TinyFish API key.\n"
        "  Mint a free key at https://agent.tinyfish.ai/api-keys\n"
        "  then run:  TINYFISH_API_KEY=<key> python3 server.py\n"
        "For verification without a key (test-only, not TinyFish usage):\n"
        "  SITE2MCP_VERIFY=1 python3 server.py\n"
    )
    sys.exit(2)


CLIENT = make_client()


# ------------------------------------------------------------------- tools

def tool_search_books(args):
    query = args.get("query", "")
    if not query:
        raise ValueError("query is required")
    max_results = int(args.get("max_results", 5))
    res = CLIENT.search(query, include_domains=["gutenberg.org"])
    books = []
    for item in res.get("results", []):
        url = item.get("url", "")
        if "gutenberg.org/ebooks/" not in url:
            continue
        no = ebook_no_from_url(url)
        if no is None:
            continue
        books.append({
            "ebook_no": no,
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": url,
        })
        if len(books) >= max_results:
            break
    out = {"query": query, "books": books, "total_results": res.get("total_results", 0)}
    if res.get("error"):
        out["search_note"] = res["error"]
    return out


def tool_get_book(args):
    ebook_no = args.get("ebook_no")
    url = args.get("url", "")
    if ebook_no is None and url:
        ebook_no = ebook_no_from_url(url)
    if ebook_no is None:
        raise ValueError("ebook_no or a gutenberg.org/ebooks/<id> url is required")
    target = book_url(ebook_no)
    # Scoped fetch: TinyFish's default reader extraction strips the bibrec
    # table's class and every href, so the parsers see nothing. Scoping to
    # the table + the download anchors returns them verbatim (classes and
    # hrefs intact) — the only change needed; gutenberg.py is untouched.
    fetched = CLIENT.fetch([target], fmt="html", include_selectors=[
        "table.bibrec", "a.other-format-link"])
    if not fetched.get("results"):
        raise ValueError(f"fetch failed: {fetched.get('errors')}")
    return parse_book_page(fetched["results"][0]["text"], page_url=target)


def tool_get_book_text(args):
    ebook_no = args.get("ebook_no")
    if ebook_no is None:
        raise ValueError("ebook_no is required")
    chars = int(args.get("chars", 1500))
    target = text_url(ebook_no)
    fetched = CLIENT.fetch([target], fmt="markdown")
    if not fetched.get("results"):
        raise ValueError(f"fetch failed: {fetched.get('errors')}")
    raw = fetched["results"][0]["text"]
    return {
        "ebook_no": int(ebook_no),
        "source_url": target,
        "excerpt_chars": chars,
        "excerpt": extract_text_excerpt(raw, chars=chars),
    }


def tool_list_new_releases(args):
    days = int(args.get("days", 7))
    target = recent_url(days)
    # Scoped fetch (see tool_get_book): the unscoped extraction strips the
    # li.pgdbetext class and every href, so no ebook numbers survive.
    # Scoping returns the list items verbatim, hrefs included.
    fetched = CLIENT.fetch([target], fmt="html",
                           include_selectors=["li.pgdbetext"])
    if not fetched.get("results"):
        raise ValueError(f"fetch failed: {fetched.get('errors')}")
    items = parse_recent_page(fetched["results"][0]["text"])
    return {"source_url": target, "days": days, "count": len(items),
            "books": items[:20]}


def tool_find_and_open(args):
    query = args.get("query", "")
    url = args.get("url", "")
    if not query and not url:
        raise ValueError("query or url is required")
    top = url
    if not top:
        res = CLIENT.search(query, include_domains=["gutenberg.org"])
        for item in res.get("results", []):
            if "gutenberg.org/ebooks/" in item.get("url", ""):
                top = item["url"]
                break
        if not top:
            return {"query": query, "found": False,
                    "note": "no gutenberg.org book page in search results"}
    goal = (
        "Open this Project Gutenberg book page and return a JSON object "
        "with keys: title, author, subjects (array of strings), "
        "download_links (array of {format, url}). Return ONLY the JSON object."
    )
    result = CLIENT.agent_stream(top, goal)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"raw_result": result}
    result["query"] = query
    result["source_url"] = top
    return result


def tool_watch_new_releases(args):
    schedule = args.get("schedule_cron", "0 9 * * *")
    purpose = args.get("purpose") or "A new public-domain ebook is posted"
    return CLIENT.create_monitor(
        recent_url(7), schedule, "Gutenberg new releases", purpose)


TOOLS = [
    ("search_books", "Search Project Gutenberg for books by title, author or "
     "subject. Uses TinyFish Search scoped to gutenberg.org.",
     {"type": "object", "properties": {
         "query": {"type": "string", "description": "e.g. 'pride and prejudice' or 'sherlock holmes'"},
         "max_results": {"type": "integer", "default": 5}},
      "required": ["query"]}, tool_search_books),
    ("get_book", "Get structured metadata for one Project Gutenberg ebook: "
     "title, author, subjects, download counts and format links. Uses "
     "TinyFish Fetch on the book page.",
     {"type": "object", "properties": {
         "ebook_no": {"type": "integer", "description": "e.g. 1342"},
         "url": {"type": "string", "description": "a gutenberg.org/ebooks/<id> URL"}},
      "required": []}, tool_get_book),
    ("get_book_text", "Return the opening excerpt of a book's plain-text "
     "edition (header skipped). Uses TinyFish Fetch on the .txt edition.",
     {"type": "object", "properties": {
         "ebook_no": {"type": "integer"},
         "chars": {"type": "integer", "default": 1500}},
      "required": ["ebook_no"]}, tool_get_book_text),
    ("list_new_releases", "List books recently posted or updated on Project "
     "Gutenberg. Uses TinyFish Fetch on the recent-postings page.",
     {"type": "object", "properties": {
         "days": {"type": "integer", "default": 7, "description": "1, 7 or 30"}},
      "required": []}, tool_list_new_releases),
    ("find_and_open", "Multi-step: search Gutenberg for a query (or open a "
     "given book URL), then extract the book page's metadata as JSON. Uses "
     "TinyFish Search then TinyFish Agent on the book page.",
     {"type": "object", "properties": {
         "query": {"type": "string"},
         "url": {"type": "string", "description": "a gutenberg.org/ebooks/<id> URL to open directly, skipping search"}},
      "required": []}, tool_find_and_open),
    ("watch_new_releases", "Create a TinyFish Page Monitor on Gutenberg's "
     "recent-postings page: a baseline is captured immediately, then it "
     "checks on schedule and reports new postings.",
     {"type": "object", "properties": {
         "schedule_cron": {"type": "string", "default": "0 9 * * *",
                           "description": "5-field cron, UTC unless CRON_TZ= prefixed"},
         "purpose": {"type": "string"}},
      "required": []}, tool_watch_new_releases),
]
TOOL_FUNCS = {name: fn for name, _, _, fn in TOOLS}


# ------------------------------------------------------------------- JSON-RPC

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def handle(msg):
    method = msg.get("method", "")
    msg_id = msg.get("id")
    if method.startswith("notifications/"):
        return  # no reply to notifications
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [
            {"name": n, "description": d, "inputSchema": s}
            for n, d, s, _ in TOOLS]}}
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        fn = TOOL_FUNCS.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32602, "message": f"unknown tool: {name}"}}
        try:
            result = fn(args)
        except Exception as e:  # noqa: BLE001 - surface tool errors via JSON-RPC
            return {"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32603, "message": f"{type(e).__name__}: {e}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [
            {"type": "text", "text": json.dumps(result, indent=2)}]}}
    return {"jsonrpc": "2.0", "id": msg_id, "error": {
        "code": -32601, "message": f"method not found: {method}"}}


def main():
    log(f"[{SERVER_NAME}] v{SERVER_VERSION} listening on stdio")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            send({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": f"parse error: {e}"}})
            continue
        reply = handle(msg)
        if reply is not None:
            send(reply)


if __name__ == "__main__":
    main()
