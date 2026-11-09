"""Project Gutenberg site adapter for Site to MCP.

Pure-stdlib parsing of gutenberg.org pages. No network calls here except
the robots.txt check (done by the caller-supplied fetcher in direct mode;
TinyFish Fetch honors robots server-side).

Live page shapes verified 2026-11-08 against:
  - https://www.gutenberg.org/ebooks/1342 (Pride and Prejudice)
  - https://www.gutenberg.org/browse/recent/last7.html.utf8
  - https://www.gutenberg.org/robots.txt  (Disallow: /ebooks/search only)

Project Gutenberg offers no official JSON REST API (the catalog is RDF/XML
dumps; Gutendex is a third-party wrapper). That is exactly why it is a fair
"website without an API" target for this bounty.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

BASE = "https://www.gutenberg.org"
SEARCH_PATH = "/ebooks/search/"  # robots.txt: Disallow for all user-agents

USER_AGENT = "TinyFishSiteMCP/0.1 (bounty demo; contact: arc.agent.ops@gmail.com)"


def book_url(ebook_no):
    return f"{BASE}/ebooks/{int(ebook_no)}"


def text_url(ebook_no):
    return f"{BASE}/ebooks/{int(ebook_no)}.txt.utf-8"


def recent_url(days=7):
    if days <= 1:
        name = "last1.html.utf8"
    elif days <= 7:
        name = "last7.html.utf8"
    else:
        name = "last30.html.utf8"
    return f"{BASE}/browse/recent/{name}"


def ebook_no_from_url(url):
    m = re.search(r"/ebooks/(\d+)", url or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- robots.txt

def parse_robots_txt(text):
    """Return the set of Disallow paths that apply to User-agent: *."""
    disallowed = set()
    current_applies = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            current_applies = value in ("*",)
        elif field == "disallow" and current_applies and value:
            disallowed.add(value)
    return disallowed


def is_disallowed(path, disallowed):
    return any(path == d or path.startswith(d) for d in disallowed)


# ---------------------------------------------------------------- book page

class _BookPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_bibrec = False
        self.in_row = False
        self.in_th = False
        self.in_td = False
        self._th = []
        self._td = []
        self.rows = []  # list of (label, value)
        self.downloads = []  # list of (href, label)
        self._a_href = None
        self._a_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and "bibrec" in (attrs.get("class") or ""):
            self.in_bibrec = True
        if self.in_bibrec and tag == "tr":
            self.in_row = True
            self._th, self._td = [], []
        if self.in_row and tag == "th":
            self.in_th = True
        if self.in_row and tag == "td":
            self.in_td = True
        if tag == "a" and attrs.get("href"):
            self._a_href = attrs["href"]
            self._a_text = []

    def handle_endtag(self, tag):
        if tag == "table" and self.in_bibrec:
            self.in_bibrec = False
        if tag == "tr" and self.in_row:
            self.in_row = False
            label = "".join(self._th).strip()
            value = re.sub(r"\s+", " ", "".join(self._td)).strip()
            if label:
                self.rows.append((label, value))
        if tag == "th":
            self.in_th = False
        if tag == "td":
            self.in_td = False
        if tag == "a" and self._a_href is not None:
            href, label = self._a_href, "".join(self._a_text).strip()
            if re.search(r"/ebooks/\d+\.(epub3\.images|txt\.utf-8|epub\.images|epub\.noimages|kf8\.images|kindle\.images)$", href) \
                    or re.search(r"/cache/epub/\d+/pg\d+-images\.html$", href) \
                    or re.search(r"/files/\d+/$", href):
                self.downloads.append((href, label))
            self._a_href = None
            self._a_text = []

    def handle_data(self, data):
        if self.in_th:
            self._th.append(data)
        if self.in_td:
            self._td.append(data)
        if self._a_href is not None:
            self._a_text.append(data)


def parse_book_page(html, page_url=""):
    """Parse a gutenberg.org /ebooks/<id> page into structured metadata."""
    parser = _BookPageParser()
    parser.feed(html or "")
    fields = {}
    subjects = []
    for label, value in parser.rows:
        if label == "Subject":
            subjects.append(value)
        elif label not in fields:
            fields[label] = value
    title = fields.get("Title", "")
    author = fields.get("Author", "")
    if not title:
        m = re.search(r'<h1[^>]*id="book_title"[^>]*>(.*?)</h1>', html or "", re.S)
        if m:
            raw = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            title = re.sub(r"\s+by\s+.*$", "", raw)
    downloads = []
    seen = set()
    for href, label in parser.downloads:
        full = urljoin(BASE, href)
        if full in seen:
            continue
        seen.add(full)
        downloads.append({"format": label or href.rsplit(".", 1)[-1], "url": full})
    return {
        "ebook_no": ebook_no_from_url(page_url) or _int_or_none(fields.get("eBook-No.")),
        "title": title,
        "author": author,
        "subjects": subjects,
        "category": fields.get("Category", ""),
        "language": "",  # bibrec does not carry a Language row on book pages
        "downloads_last_30_days": fields.get("Downloads", ""),
        "copyright": fields.get("Copyright", ""),
        "download_links": downloads,
        "source_url": page_url or book_url(fields.get("eBook-No.", "")),
    }


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- recent list

class _RecentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_item = False
        self._a_href = None
        self._text = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and "pgdbetext" in (attrs.get("class") or ""):
            self.in_item = True
            self._text = []
        if self.in_item and tag == "a" and attrs.get("href"):
            self._a_href = attrs["href"]

    def handle_endtag(self, tag):
        if tag == "li" and self.in_item:
            self.in_item = False
            raw = re.sub(r"\s+", " ", "".join(self._text)).strip()
            # "Title (English) (as Author)" -> title, language, role
            m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*\(as ([^)]+)\)\s*$", raw)
            if m:
                title, language, role = m.group(1), m.group(2), m.group(3)
            else:
                title, language, role = raw, "", ""
            no = ebook_no_from_url(self._a_href or "")
            if no:
                self.items.append({
                    "ebook_no": no,
                    "title": title,
                    "language": language,
                    "role": role,
                    "url": urljoin(BASE, self._a_href),
                })
            self._a_href = None

    def handle_data(self, data):
        if self.in_item:
            self._text.append(data)


def parse_recent_page(html):
    parser = _RecentParser()
    parser.feed(html or "")
    return parser.items


# ---------------------------------------------------------------- plain text

_START_MARKERS = (
    "*** START OF THE PROJECT GUTENBERG EBOOK",
    "*** START OF THIS PROJECT GUTENBERG EBOOK",
)

_CHAPTER_RE = re.compile(
    r"(?m)^(Chapter\s+[0-9IVXLC]+|CHAPTER\s+[0-9IVXLC]+)\b", re.M)


def extract_text_excerpt(text, chars=1500):
    """Return the opening of the book body: skip the Gutenberg header, then
    skip front matter (title page, illustration lists, contents) up to the
    first chapter heading when one appears within the next 12,000 chars.
    Falls back to the text right after the header when no chapter heading
    is found (poetry, essays, short works)."""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    upper = body.upper()
    start = -1
    for marker in _START_MARKERS:
        idx = upper.find(marker)
        if idx != -1:
            start = idx
            break
    if start != -1:
        nl = body.find("\n", start)
        body = body[nl + 1:] if nl != -1 else body[start:]
    body = body.lstrip("\n ")
    m = _CHAPTER_RE.search(body[:60000])
    if m:
        body = body[m.start():]
    return body[:chars].strip()
