#!/usr/bin/env python3
"""Live verification harness for Site to MCP.

Drives server.py over stdio and checks every tool against LIVE
gutenberg.org pages. Records evidence in verification/evidence.jsonl.

Modes:
  --mode verify    DirectClient stand-in (no key needed). Proves the MCP
                   server, the tool logic and the Gutenberg parsing live.
                   Search may come back DEGRADED: the sandbox's datacenter
                   IP is bot-gated by DuckDuckGo Lite, so the stand-in
                   search engine can be blocked. The search_books LOGIC is
                   then proven by a canned-response unit check instead.
  --mode tinyfish  Real TinyFish API (needs TINYFISH_API_KEY). Same checks
                   against TinyFish. Creates ONE real Page Monitor — its id
                   is printed so it can be deleted from the dashboard.

Exit 0 only if no check FAILs (DEGRADED is allowed, with the reason
recorded).
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.join(HERE, "verification", "evidence.jsonl")


def run_server_no_key():
    """Server must refuse to start without a key and without VERIFY."""
    env = dict(os.environ)
    env.pop("TINYFISH_API_KEY", None)
    env.pop("SITE2MCP_VERIFY", None)
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "server.py")],
        input="", capture_output=True, text=True, timeout=30, env=env)
    ok = p.returncode == 2 and "TINYFISH_API_KEY" in p.stderr
    return ok, p.returncode, p.stderr[:200]


def unit_search_books_logic():
    """Prove search_books parsing/filtering and find_and_open composition
    with a canned TinyFish-shaped response (used when the live stand-in
    search engine is bot-gated)."""
    os.environ["SITE2MCP_VERIFY"] = "1"
    sys.path.insert(0, HERE)
    import server

    class Stub:
        def search(self, query, include_domains=None, language="en", page=0):
            return {"query": query, "total_results": 2, "results": [
                {"position": 1, "site_name": "gutenberg.org",
                 "title": "Pride and Prejudice by Jane Austen",
                 "snippet": "A novel.", "url": "https://www.gutenberg.org/ebooks/1342"},
                {"position": 2, "site_name": "example.com",
                 "title": "Unrelated", "snippet": "",
                 "url": "https://example.com/not-a-book"},
            ]}

        def agent_stream(self, url, goal, browser_profile="lite", timeout=300):
            return {"title": "Pride and Prejudice",
                    "author": "Austen, Jane, 1775-1817",
                    "subjects": ["England -- Fiction"],
                    "download_links": [{"format": "Plain Text",
                                        "url": "https://www.gutenberg.org/ebooks/1342.txt.utf-8"}]}

    server.CLIENT = Stub()
    out = server.tool_search_books({"query": "pride and prejudice"})
    search_ok = (len(out["books"]) == 1
                 and out["books"][0]["ebook_no"] == 1342
                 and "pride" in out["books"][0]["title"].lower())
    opened = server.tool_find_and_open({"query": "pride and prejudice"})
    open_ok = (opened.get("title") == "Pride and Prejudice"
               and opened.get("source_url") == "https://www.gutenberg.org/ebooks/1342"
               and len(opened.get("download_links", [])) >= 1)
    return search_ok and open_ok


def main():
    ap = argparse.ArgumentParser(description="Live verification for Site to MCP")
    ap.add_argument("--mode", choices=["verify", "tinyfish"], default="verify",
                    help="verify: DirectClient stand-in (no key needed). "
                         "tinyfish: real TinyFish API (needs TINYFISH_API_KEY; "
                         "creates ONE real Page Monitor — note its id).")
    args = ap.parse_args()
    mode = args.mode
    os.makedirs(os.path.dirname(EVIDENCE), exist_ok=True)
    checks = []  # (name, status, detail); status in PASS/FAIL/DEGRADED
    if mode == "tinyfish" and not os.environ.get("TINYFISH_API_KEY"):
        print("tinyfish mode needs TINYFISH_API_KEY in the environment.")
        return 2

    if mode == "verify":
        ok, code, err = run_server_no_key()
        checks.append(("server refuses start without key (exit 2)",
                       "PASS" if ok else "FAIL",
                       f"exit={code}" if not ok else "exit 2, key instructions on stderr"))
    else:
        checks.append(("server refuses start without key (exit 2)", "PASS",
                       "skipped in tinyfish mode — key is present by definition"))

    # ---- fixed live request sequence -------------------------------------
    requests = [
        (1, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "verify", "version": "0.1"}}}),
        (None, {"jsonrpc": "2.0", "method": "notifications/initialized"}),
        (2, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        (3, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_books",
                        "arguments": {"query": "pride and prejudice"}}}),
        (4, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "get_book", "arguments": {"ebook_no": 1342}}}),
        (5, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "get_book_text",
                        "arguments": {"ebook_no": 1342, "chars": 800}}}),
        (6, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "list_new_releases", "arguments": {"days": 7}}}),
        (7, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "find_and_open",
                        "arguments": {"url": "https://www.gutenberg.org/ebooks/1661"}}}),
        (8, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
             "params": {"name": "watch_new_releases",
                        "arguments": {"schedule_cron": "0 9 * * *",
                                      "purpose": "A new public-domain ebook is posted"}}}),
        (9, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": "watch_new_releases",
                        "arguments": {"schedule_cron": "0 9 * * *",
                                      "purpose": "A new public-domain ebook is posted"}}}),
    ]
    # In tinyfish mode, run watch_new_releases only ONCE: a second call
    # would create a second real (metered, scheduled) monitor.
    if mode == "tinyfish":
        requests = [r for r in requests if r[0] != 9]
    payload = "\n".join(json.dumps(r) for _, r in requests) + "\n"
    env = dict(os.environ)
    if mode == "verify":
        env.pop("TINYFISH_API_KEY", None)
        env["SITE2MCP_VERIFY"] = "1"
    else:
        env.pop("SITE2MCP_VERIFY", None)
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "server.py")],
        input=payload, capture_output=True, text=True, timeout=300, env=env)
    elapsed = time.time() - t0

    replies = {}
    for line in p.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg and msg["id"] is not None:
            replies[msg["id"]] = msg

    def tool_result(rid):
        msg = replies.get(rid, {})
        if "error" in msg:
            return None, msg["error"]
        content = msg.get("result", {}).get("content", [])
        if not content:
            return None, {"message": "empty content"}
        try:
            return json.loads(content[0]["text"]), None
        except (json.JSONDecodeError, KeyError) as e:
            return None, {"message": f"bad tool payload: {e}"}

    def record(tool, status, detail):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "tool": tool, "status": status, "detail": str(detail)[:600]}
        with open(EVIDENCE, "a") as f:
            f.write(json.dumps(rec) + "\n")
        checks.append((tool, status, rec["detail"]))

    # ---- evaluate ---------------------------------------------------------
    tools = replies.get(2, {}).get("result", {}).get("tools", [])
    record("tools/list returns 6 tools", "PASS" if len(tools) == 6 else "FAIL",
           f"{len(tools)} tools: {[t.get('name') for t in tools]}")

    res, err = tool_result(3)
    if err:
        record("search_books", "FAIL", err)
    elif res.get("search_note"):
        # Stand-in search engine blocked (bot-gated from this sandbox).
        # Prove the logic separately with a canned response.
        try:
            logic_ok = unit_search_books_logic()
        except Exception as e:  # noqa: BLE001
            logic_ok = False
            logic_err = str(e)
        else:
            logic_err = ""
        record("search_books live (stand-in engine)",
               "DEGRADED",
               f"stand-in search blocked: {res['search_note']}; "
               f"logic unit check {'PASS' if logic_ok else 'FAIL ' + logic_err}")
        record("search_books logic (canned TinyFish response)",
               "PASS" if logic_ok else "FAIL",
               "search filters to gutenberg.org URLs and extracts ebook_no; "
               "find_and_open composes search -> agent -> JSON"
               if logic_ok else logic_err)
    else:
        books = res.get("books", [])
        good = (len(books) >= 1
                and all("gutenberg.org/ebooks/" in b["url"] for b in books)
                and any("pride" in b["title"].lower() for b in books))
        record("search_books finds Pride and Prejudice live",
               "PASS" if good else "FAIL",
               f"{len(books)} books, first={books[0]['title'] if books else None}")

    res, err = tool_result(4)
    if err:
        record("get_book", "FAIL", err)
    else:
        good = (res.get("title") == "Pride and Prejudice"
                and "Austen" in res.get("author", "")
                and res.get("ebook_no") == 1342
                and len(res.get("download_links", [])) >= 3)
        record("get_book returns real metadata for #1342",
               "PASS" if good else "FAIL",
               f"title={res.get('title')!r} author={res.get('author')!r} "
               f"links={len(res.get('download_links', []))} "
               f"downloads={res.get('downloads_last_30_days')!r}")

    res, err = tool_result(5)
    if err:
        record("get_book_text", "FAIL", err)
    else:
        excerpt = res.get("excerpt", "")
        good = "It is a truth universally acknowledged" in excerpt
        record("get_book_text returns the real opening of P&P",
               "PASS" if good else "FAIL",
               f"excerpt[{len(excerpt)}]={excerpt[:120]!r}")

    res, err = tool_result(6)
    if err:
        record("list_new_releases", "FAIL", err)
    else:
        books = res.get("books", [])
        good = len(books) > 0 and all(b.get("ebook_no") and b.get("title") for b in books)
        record("list_new_releases returns live postings",
               "PASS" if good else "FAIL",
               f"count={res.get('count')} first={books[0] if books else None}")

    res, err = tool_result(7)
    if err:
        record("find_and_open", "FAIL", err)
    else:
        good = (bool(res.get("title"))
                and "sherlock" in res.get("title", "").lower()
                and len(res.get("download_links", [])) >= 1)
        record("find_and_open opens #1661 live (Sherlock Holmes)",
               "PASS" if good else "FAIL",
               f"title={res.get('title')!r} author={res.get('author')!r}")

    res, err = tool_result(8)
    if err:
        record("watch_new_releases (1st call)", "FAIL", err)
    elif mode == "tinyfish":
        mid = res.get("monitor_id") or res.get("id") or (
            res.get("monitor") or {}).get("id")
        record("watch_new_releases creates a real TinyFish monitor",
               "PASS" if mid else "FAIL",
               f"monitor_id={mid} — delete it from the TinyFish dashboard "
               "if you do not want the daily schedule to keep running; "
               f"raw={str(res)[:200]}")
    else:
        record("watch_new_releases captures a baseline",
               "PASS" if res.get("outcome") == "baseline captured" else "FAIL",
               f"outcome={res.get('outcome')!r} items={res.get('baseline_items')}")

    if mode == "verify":
        res, err = tool_result(9)
        if err:
            record("watch_new_releases (2nd call)", "FAIL", err)
        else:
            record("watch_new_releases diffs against baseline",
                   "PASS" if res.get("outcome") in ("no changes", "changed") else "FAIL",
                   f"outcome={res.get('outcome')!r} new={res.get('new_ebook_nos')}")

    # ---- summary ----------------------------------------------------------
    n_pass = sum(1 for _, s, _ in checks if s == "PASS")
    n_deg = sum(1 for _, s, _ in checks if s == "DEGRADED")
    n_fail = sum(1 for _, s, _ in checks if s == "FAIL")
    print(f"\nSite to MCP live verification — {elapsed:.1f}s, mode={mode} "
          f"({'DirectClient stand-in' if mode == 'verify' else 'real TinyFish API'})")
    for name, status, detail in checks:
        print(f"  {status:8} {name}  — {detail}")
    print(f"\n{n_pass} passed, {n_deg} degraded, {n_fail} failed "
          f"({len(checks)} checks). Evidence: verification/evidence.jsonl")
    if p.stderr.strip():
        print("Server stderr (first 300 chars):", p.stderr[:300].replace("\n", " | "))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
