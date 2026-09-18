"""
Core on-page keyword SEO checks (Seobility-style). Pure logic, no I/O framework
dependency, so it's shared by both the CLI script and the web app.
"""
import html as html_lib
import re
import time
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

STOP_WORDS = {
    "a", "about", "above", "after", "again", "all", "am", "an", "and", "any",
    "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "can", "could", "did", "do", "does",
    "doing", "down", "during", "each", "few", "for", "from", "further", "had",
    "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its",
    "itself", "just", "me", "more", "most", "my", "myself", "no", "nor",
    "not", "now", "of", "off", "on", "once", "only", "or", "other", "our",
    "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so",
    "some", "such", "than", "that", "the", "their", "theirs", "them",
    "themselves", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "under", "until", "up", "us", "very", "was",
    "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why",
    "will", "with", "would", "you", "your", "yours", "yourself",
    "yourselves",
}


class RenderedResponse:
    """Mimics the bits of requests.Response that run_check() reads, backed by
    the DOM a headless browser produced after running the page's JS."""

    def __init__(self, status_code, html, url):
        self.status_code = status_code
        self.text = html
        self.content = html.encode("utf-8")
        self.url = url

    def raise_for_status(self):
        if self.status_code and self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error for url: {self.url}")


def fetch(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SEOKeywordCheck/1.0)"}
    start = time.perf_counter()
    resp = requests.get(url, headers=headers, timeout=20)
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    return resp, elapsed


def fetch_rendered(url, wait_ms=1500):
    """Load the page in headless Chromium and return the fully rendered DOM,
    for pages whose content is injected by JavaScript (SPAs)."""
    from playwright.sync_api import sync_playwright

    start = time.perf_counter()
    with sync_playwright() as pw:
        # --no-sandbox: Chromium's sandbox needs kernel namespace features
        # that containers running as root typically don't grant; without
        # this it hangs/crashes on launch instead of erroring cleanly.
        browser = pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(user_agent="Mozilla/5.0 (compatible; SEOKeywordCheck/1.0)")
            response = page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            status_code = response.status if response else None
        finally:
            browser.close()
    elapsed = time.perf_counter() - start
    resp = RenderedResponse(status_code, html, url)
    resp.raise_for_status()
    return resp, elapsed


MAX_KEYWORD_GAP_CHARS = 60


def build_keyword_regex(keyword):
    """Compile a regex matching a keyword phrase's words in order, allowing
    up to MAX_KEYWORD_GAP_CHARS of other text between them (e.g. "Personal AI
    Assistant" also matches "...personal, AI-powered virtual assistant...").
    The cap keeps matches meaningfully close together rather than treating
    words scattered across an entire page as one "phrase". Each word is its
    own capture group so callers can bold them individually. A single-word
    keyword behaves like a plain substring search, same as before."""
    words = [w for w in keyword.split() if w]
    if not words:
        return None
    gap = r".{0,%d}?" % MAX_KEYWORD_GAP_CHARS
    pattern = "(" + re.escape(words[0]) + ")"
    for w in words[1:]:
        pattern += gap + "(" + re.escape(w) + ")"
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


def _valid_word_edge(text, start, end):
    """A substring match at [start, end) counts as a real word occurrence if
    each side is either a true boundary (start/end of string, or a non-alnum
    neighbor) or a camelCase-style transition - e.g. "edge" inside
    "FastEdge" (lowercase->uppercase into the match... match starts at 'E'
    preceded by lowercase 't') or "cdn" inside "BunnyCDN". This is what lets
    compound brand terms match while rejecting coincidental substrings like
    "our" inside "Your" (no case transition and no true boundary there)."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    match_first = text[start]
    match_last = text[end - 1]
    left_ok = (not before) or (not before.isalnum()) or (match_first.isupper() and before.islower())
    right_ok = (not after) or (not after.isalnum()) or (match_last.islower() and after.isupper())
    return left_ok and right_ok


def find_word(text, word):
    """First real occurrence of a single word in text (see _valid_word_edge).
    Returns a (start, end) tuple, or None."""
    if not text or not word:
        return None
    for m in re.finditer(re.escape(word), text, re.IGNORECASE):
        if _valid_word_edge(text, m.start(), m.end()):
            return m.start(), m.end()
    return None


def find_all_word(text, word):
    if not text or not word:
        return []
    return [(m.start(), m.end()) for m in re.finditer(re.escape(word), text, re.IGNORECASE)
            if _valid_word_edge(text, m.start(), m.end())]


def bold(value):
    """Escape and wrap a dynamic value (e.g. a keyword word) in <strong>,
    for building issue messages that mix static text with |safe HTML."""
    return f"<strong>{html_lib.escape(str(value))}</strong>"


def issue(level, text):
    """One issue/finding entry. level is 'good' (green check), 'warn'
    (yellow exclamation), 'bad' (red cross), or 'info' (plain, no icon)."""
    return {"level": level, "text": text}


def contains_kw(text, keyword):
    if not text:
        return False
    words = keyword_words(keyword)
    if len(words) <= 1:
        return find_word(text, keyword) is not None
    regex = build_keyword_regex(keyword)
    return regex is not None and regex.search(text) is not None


def highlight(text, keyword):
    """HTML-escape `text` and wrap each case-insensitive keyword word in
    <strong> (leaving any in-between text plain), so callers can render it
    with |safe. If the keyword isn't present, returns the escaped text with
    nothing bolded."""
    if not text:
        return ""
    regex = build_keyword_regex(keyword)
    m = regex.search(text) if regex else None
    if not m:
        return html_lib.escape(text)
    spans = [m.span(i) for i in range(1, m.re.groups + 1)]
    pieces = []
    last_end = 0
    for start, end in spans:
        pieces.append(html_lib.escape(text[last_end:start]))
        pieces.append("<strong>" + html_lib.escape(text[start:end]) + "</strong>")
        last_end = end
    pieces.append(html_lib.escape(text[last_end:]))
    return "".join(pieces)


def keyword_words(keyword):
    return [w for w in keyword.split() if w]


def highlight_words(text, words):
    """HTML-escape `text` and bold the first case-insensitive occurrence of
    each word in `words` independently, wherever it appears (no order or
    proximity required). Overlapping/touching matches (e.g. "fast" and
    "edge" both inside "FastEdge") are merged into one bold run."""
    if not text:
        return ""
    spans = []
    for w in words:
        span = find_word(text, w)
        if span:
            spans.append(span)
    if not spans:
        return html_lib.escape(text)
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    pieces = []
    last_end = 0
    for s, e in merged:
        pieces.append(html_lib.escape(text[last_end:s]))
        pieces.append("<strong>" + html_lib.escape(text[s:e]) + "</strong>")
        last_end = e
    pieces.append(html_lib.escape(text[last_end:]))
    return "".join(pieces)


def context_snippet(parent_text, tag_text, keyword, window=4):
    """Build an escaped '<4 words> **keyword** <4 words>' snippet locating
    `keyword` inside `tag_text`, using `parent_text` to supply the
    surrounding words for context. Each keyword word is bolded individually
    so gapped phrases (see build_keyword_regex) render correctly."""
    regex = build_keyword_regex(keyword)
    if regex is None:
        return None

    tag_start = parent_text.find(tag_text) if tag_text else -1
    search_text = parent_text
    match = None
    if tag_start != -1:
        tag_end = tag_start + len(tag_text)
        for m in regex.finditer(parent_text):
            if m.start() < tag_end and m.end() > tag_start:
                match = m
                break
    if match is None:
        search_text = tag_text
        match = regex.search(tag_text)
    if match is None:
        return None

    spans = [match.span(i) for i in range(1, match.re.groups + 1)]
    start, end = spans[0][0], spans[-1][1]

    before_words = search_text[:start].split()
    after_words = search_text[end:].split()
    before = " ".join(before_words[-window:]) if before_words else ""
    after = " ".join(after_words[:window]) if after_words else ""

    parts = []
    if len(before_words) > window:
        parts.append("… ")
    if before:
        parts.append(html_lib.escape(before) + " ")

    last_end = start
    for s, e in spans:
        if s > last_end:
            parts.append(html_lib.escape(search_text[last_end:s]))
        parts.append(f"<strong>{html_lib.escape(search_text[s:e])}</strong>")
        last_end = e
    if after:
        parts.append(" " + html_lib.escape(after))
    if len(after_words) > window:
        parts.append(" …")
    return "".join(parts)


def check_title(soup, keyword):
    """Each keyword word is checked independently (any order, no adjacency
    required), plus one shared point for title length - but it's all or
    nothing: any missing part (length or any word) zeroes the whole check,
    even though the breakdown still shows which individual parts passed.
    max = 1 + n_words."""
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    kw_words = keyword_words(keyword)
    ok_length = 10 <= len(title) <= 65
    max_score = 1 + len(kw_words)
    issues = []
    raw_score = 0
    if not title:
        issues.append(issue("bad", "Missing in the HTML"))
    else:
        raw_score += int(ok_length)
        if not ok_length:
            issues.append(issue("warn", f"Title length is {len(title)} characters (recommended 10-65)"))
        for w in kw_words:
            if contains_kw(title, w):
                raw_score += 1
            else:
                issues.append(issue("bad", f"Missing in the title: {bold(w)}"))
    score = raw_score if raw_score == max_score else 0
    return {"name": "Title", "importance": "Very important", "score": score, "max": max_score,
            "value": title, "highlight": highlight_words(title, kw_words), "issues": issues}


def check_meta_description(soup, keyword):
    """Each keyword word is checked independently (any order, no adjacency
    required), plus one shared point for the description existing.
    max = 1 + n_words. Position within the first 120 characters is reported
    per word but is informational only, not scored."""
    tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = tag.get("content", "").strip() if tag else ""
    kw_words = keyword_words(keyword)
    issues = []
    score = 0
    if not desc:
        issues.append(issue("bad", "Missing in the HTML"))
    else:
        score += 1
        for w in kw_words:
            span = find_word(desc, w)
            if not span:
                issues.append(issue("bad", f"Missing in the meta description: {bold(w)}"))
            else:
                score += 1
                if span[0] >= 120:
                    issues.append(issue("warn", f"Not within the first 120 characters: {bold(w)}"))
    return {"name": "Meta description", "importance": "Very important", "score": score, "max": 1 + len(kw_words),
            "value": desc, "highlight": highlight_words(desc, kw_words), "issues": issues}


def check_content(soup, keyword):
    """Single-word keyword: found (+1), well-positioned early in the text
    (+1), and a shared frequency/density check (+1) - max 3. Multi-word
    keyword: no shared frequency check - just one point per word for being
    found early enough - max = n_words (matches Seobility, which only shows
    a "well positioned" line per word and no frequency line for phrases).
    "Early enough" is relative to the page's own length, not a flat cutoff,
    since long pages still call a keyword 15-20% into the text "well
    positioned"."""
    text = soup.get_text(" ", strip=True)
    words = text.split()
    word_count = len(words)
    kw_words = keyword_words(keyword)
    multi = len(kw_words) > 1
    max_score = len(kw_words) if multi else 3

    if word_count == 0:
        return {"name": "Content", "importance": "Very important", "score": 0, "max": max_score,
                "value": None, "issues": [issue("bad", "Missing in the HTML")]}, word_count, 0

    position_cutoff = max(100, round(word_count * 0.2))

    issues = []
    score = 0
    total_occurrences = 0
    for w in kw_words:
        positions = [start for start, _ in find_all_word(text, w)]
        total_occurrences += len(positions)
        if not positions:
            issues.append(issue("bad", f"Missing in the HTML: {bold(w)}"))
            continue
        first_word_pos = len(text[:positions[0]].split()) + 1
        well_positioned = first_word_pos <= position_cutoff
        if not multi:
            score += 1
        if well_positioned:
            score += 1
            issues.append(issue("good", f"Well positioned in the text: {bold(w)} ({first_word_pos} position in text)"))
        else:
            issues.append(issue("warn", f"Keyword found late in the text: {bold(w)} ({first_word_pos} position in text)"))

    if multi and total_occurrences and all(find_word(text, w) for w in kw_words):
        issues.append(issue("good", "The search phrase is completely present in the text."))

    literal_occurrences = total_occurrences
    if not multi:
        # Frequency/density uses literal occurrences of the single word -
        # same as total_occurrences here since there's only one word.
        literal_occurrences = len(find_all_word(text, keyword))
        density = literal_occurrences / word_count
        freq_ok = 0.001 <= density <= 0.03
        issues.append(issue("info", f"The keyword is used {literal_occurrences} times in {word_count} words."))
        if freq_ok:
            score += 1
            issues.append(issue("good", "The keyword appears frequently enough in the text."))
        elif density < 0.001:
            issues.append(issue("warn", "The keyword does not appear frequently enough in the text."))
        else:
            issues.append(issue("warn", "The keyword may be used too frequently in the text (keyword stuffing)."))

    return {"name": "Content", "importance": "Very important", "score": score, "max": max_score,
            "value": None, "issues": issues}, word_count, literal_occurrences


def check_strong_tags(soup, keyword):
    tags = soup.find_all(["strong", "b"])
    if not tags:
        # Not applicable when the page has no strong/bold tags at all,
        # rather than penalizing it as a missed keyword placement.
        return {"name": "Strong and bold tags", "importance": "Low importance", "score": 0, "max": 0,
                "value": None, "snippets": [], "issues": [issue("info", "No strong or bold tags found on this page.")]}

    snippets = []
    seen = set()
    for t in tags:
        t_text = t.get_text(" ", strip=True)
        if not t_text or not contains_kw(t_text, keyword):
            continue
        parent = t.parent
        parent_text = parent.get_text(" ", strip=True) if parent else t_text
        snippet = context_snippet(parent_text, t_text, keyword)
        if snippet and snippet not in seen:
            seen.add(snippet)
            snippets.append(snippet)

    found = bool(snippets)
    issues = [] if found else [issue("bad", f"The keyword is not used in strong or bold tags: {bold(keyword)}")]
    return {"name": "Strong and bold tags", "importance": "Low importance", "score": int(found), "max": 1,
            "value": None, "snippets": snippets, "issues": issues}


def check_h1(soup, keyword):
    h1 = soup.find("h1")
    h1_text = h1.get_text(" ", strip=True) if h1 else ""
    exists = h1 is not None
    kw_words = keyword_words(keyword)
    issues = []
    score = int(exists)
    if not exists:
        issues.append(issue("bad", "Missing in the HTML"))
    else:
        for w in kw_words:
            if contains_kw(h1_text, w):
                score += 1
            else:
                issues.append(issue("bad", f"This keyword is missing in the first H1 heading: {bold(w)}"))
    return {"name": "H1 heading", "importance": "Very important", "score": score, "max": 1 + len(kw_words),
            "value": h1_text, "highlight": highlight_words(h1_text, kw_words), "issues": issues}


def check_images(soup, keyword, page_url):
    """Each keyword word is checked independently against each image's alt
    text, URL, and title (matching Seobility, which reports per-word hits
    across all three fields rather than requiring the whole phrase
    together). max = 3 * n_words."""
    kw_words = keyword_words(keyword)
    matches = []
    alt_words_found = set()
    url_words_found = set()
    title_words_found = set()
    for img in soup.find_all("img"):
        alt_text = img.get("alt", "").strip()
        title_text = img.get("title", "").strip()
        src = img.get("src", "")
        alt_hits = [w for w in kw_words if contains_kw(alt_text, w)]
        url_hits = [w for w in kw_words if contains_kw(src, w)]
        title_hits = [w for w in kw_words if contains_kw(title_text, w)]
        if alt_hits or url_hits or title_hits:
            alt_words_found.update(alt_hits)
            url_words_found.update(url_hits)
            title_words_found.update(title_hits)
            matches.append({
                "src": src or "(no src)",
                "abs_src": urljoin(page_url, src) if src else None,
                "alt_ok": bool(alt_hits),
                "url_ok": bool(url_hits),
                "title_ok": bool(title_hits),
                "alt_highlight": highlight_words(alt_text, kw_words) if alt_text else None,
                "title_highlight": highlight_words(title_text, kw_words) if title_text else None,
            })

    score = len(alt_words_found) + len(url_words_found) + len(title_words_found)
    max_score = 3 * len(kw_words)
    issues = []
    if not alt_words_found:
        issues.append(issue("bad", f"Not found in any alt attribute of images: {bold(keyword)}"))
    if not url_words_found:
        issues.append(issue("bad", f"Not found in any image URL: {bold(keyword)}"))
    if not title_words_found:
        issues.append(issue("bad", f"Not found in any image title: {bold(keyword)}"))
    return {"name": "Image SEO", "importance": "Low importance", "score": score, "max": max_score,
            "value": None, "images": matches, "issues": issues}


def check_headings_h2_h6(soup, keyword):
    kw_words = keyword_words(keyword)
    matches = []
    words_found = set()
    for tag in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text(" ", strip=True)
        hits = [w for w in kw_words if contains_kw(text, w)]
        if hits:
            words_found.update(w.lower() for w in hits)
            matches.append({"level": tag.name.upper(), "highlight": highlight_words(text, kw_words)})

    missing = [w for w in kw_words if w.lower() not in words_found]
    issues = [issue("bad", f"Not used within the H2-H6 headings: {bold(w)}") for w in missing]
    return {"name": "Headings (H2-H6)", "importance": "Important", "score": len(words_found), "max": len(kw_words),
            "value": None, "headings": matches, "issues": issues}


def check_domain(url, keyword):
    domain = urlparse(url).netloc
    kw_words = keyword_words(keyword)
    issues = []
    score = 0
    for w in kw_words:
        if contains_kw(domain, w):
            score += 1
            issues.append(issue("good", f"This keyword was found in the domain: {bold(w)}"))
        else:
            issues.append(issue("warn", f"Missing in the domain: {bold(w)}"))
    return {"name": "Domain", "importance": "Nice to have", "score": score, "max": len(kw_words),
            "value": domain, "issues": issues}


def check_stop_words(keyword):
    kw_words = keyword.lower().split()
    stop_hits = [w for w in kw_words if w in STOP_WORDS]
    is_stop = bool(stop_hits)
    issues = [issue("warn", f"The following keyword is a stop word: {bold(', '.join(stop_hits))}")] if is_stop \
        else [issue("good", "None of the keywords is a known stop word.")]
    return {"name": "Stop words", "importance": "Nice to have", "score": int(not is_stop), "max": 1,
            "value": None, "issues": issues}


def check_page_url(url, keyword):
    path = urlparse(url).path
    kw_words = keyword_words(keyword)
    issues = []
    score = 0
    for w in kw_words:
        if contains_kw(path, w):
            score += 1
            issues.append(issue("good", f"This keyword was found in the URL: {bold(w)}"))
        else:
            issues.append(issue("bad", f"This keyword was not found in the URL: {bold(w)}"))
    return {"name": "Page URL", "importance": "Low importance", "score": score, "max": len(kw_words),
            "value": path, "issues": issues}


def pct(score, max_score):
    return round(100 * score / max_score) if max_score else 0


def word_match_pct(text, kw_words):
    """% of the keyword's words present in `text`, independent of any other
    scoring on that field (e.g. title length, description existing).
    Matches how Seobility appears to weight the Meta data group."""
    if not kw_words:
        return 100
    found = sum(1 for w in kw_words if contains_kw(text, w))
    return round(100 * found / len(kw_words))


IMPORTANCE_WEIGHT = {"Very important": 3, "Important": 2, "Low importance": 1, "Nice to have": 1}


def importance_weighted_pct(checks):
    """Group % as a weighted average of each check's own score/max, weighted
    by its importance tier - not a plain sum of scores over sum of maxes.
    Checks that don't apply to the page (max == 0) are left out entirely.
    Matches how Seobility weights its HTML optimization group."""
    total = 0.0
    total_weight = 0
    for c in checks:
        if c["max"] == 0:
            continue
        weight = IMPORTANCE_WEIGHT.get(c["importance"], 1)
        total += weight * (100 * c["score"] / c["max"])
        total_weight += weight
    return round(total / total_weight) if total_weight else 0


def score_keyword(soup, keyword, url):
    """Run every check against an already-parsed page for one keyword.
    Split out from run_check so the top-keywords scan can score many
    candidate keywords against the same page without re-fetching it."""
    title_res = check_title(soup, keyword)
    desc_res = check_meta_description(soup, keyword)
    content_res, wc, occ = check_content(soup, keyword)
    strong_res = check_strong_tags(soup, keyword)
    h1_res = check_h1(soup, keyword)
    img_res = check_images(soup, keyword, url)
    headings_res = check_headings_h2_h6(soup, keyword)
    domain_res = check_domain(url, keyword)
    stopword_res = check_stop_words(keyword)
    pageurl_res = check_page_url(url, keyword)

    groups = {
        "Meta data": [title_res, desc_res],
        "HTML optimization": [h1_res, headings_res, strong_res, img_res, content_res],
        "Other": [domain_res, stopword_res, pageurl_res],
    }

    kw_words = keyword_words(keyword)

    group_summaries = {}
    total_score = 0
    total_max = 0
    issue_count = 0
    for name, checks in groups.items():
        s = sum(c["score"] for c in checks)
        m = sum(c["max"] for c in checks)
        group_pct = pct(s, m)
        if name == "Meta data":
            # Seobility appears to score this group purely on keyword-word
            # presence in title vs. description, dropping the "exists"/
            # length bonus points that the individual checks still display.
            title_pct = word_match_pct(title_res["value"], kw_words)
            desc_pct = word_match_pct(desc_res["value"], kw_words)
            group_pct = round((title_pct + desc_pct) / 2)
        elif name == "Other":
            # Domain appears double-weighted relative to stop words/page URL.
            domain_pct = pct(domain_res["score"], domain_res["max"])
            stopword_pct = pct(stopword_res["score"], stopword_res["max"])
            pageurl_pct = pct(pageurl_res["score"], pageurl_res["max"])
            group_pct = round((2 * domain_pct + stopword_pct + pageurl_pct) / 4)
        elif name == "HTML optimization":
            group_pct = importance_weighted_pct(checks)
        group_summaries[name] = {"score": s, "max": m, "pct": group_pct, "checks": checks}
        total_score += s
        total_max += m
        issue_count += sum(1 for c in checks if c["score"] < c["max"] and c["importance"] != "Nice to have")

    return group_summaries, total_score, total_max, issue_count


def extract_headings_outline(soup, keyword=None):
    """Full H1-H6 outline in document order, with the keyword (if any)
    highlighted and simple structural issues flagged (empty heading,
    skipped level, more than one H1)."""
    kw_words = keyword_words(keyword) if keyword else []
    outline = []
    prev_level = 0
    h1_count = 0
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(tag.name[1])
        text = tag.get_text(" ", strip=True)
        issues = []
        if not text:
            issues.append("Empty heading")
        if level == 1:
            h1_count += 1
        if prev_level and level > prev_level + 1:
            issues.append(f"Skipped heading level (H{prev_level} to H{level})")
        prev_level = level
        outline.append({
            "level": tag.name.upper(),
            "text": text,
            "highlight": highlight_words(text, kw_words) if kw_words else html_lib.escape(text),
            "issues": issues,
        })
    if h1_count > 1:
        for item in outline:
            if item["level"] == "H1":
                item["issues"].append("Multiple H1 headings found on this page")
    return outline


def extract_text_blocks(soup, min_len=100):
    """Paragraph/list-item text blocks over min_len characters, flagging
    ones that appear more than once on the page (candidate boilerplate).
    Skips nav/header/footer and link-heavy blocks (mega-menus), which are
    navigation chrome rather than real content."""
    texts = []
    for tag in soup.find_all(["p", "li"]):
        if tag.find_parent(["nav", "header", "footer"]):
            continue
        text = tag.get_text(" ", strip=True)
        if len(text) < min_len:
            continue
        link_text_len = sum(len(a.get_text(strip=True)) for a in tag.find_all("a"))
        if link_text_len > 0.5 * len(text):
            continue
        texts.append(text)

    counts = Counter(texts)
    blocks = []
    seen = set()
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        blocks.append({"text": text, "word_count": len(text.split()), "repeated": counts[text] > 1})
    return blocks


def extract_media_files(soup, page_url):
    """Every distinct <img> on the page with its resolved URL, alt and title."""
    files = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src:
            continue
        abs_src = urljoin(page_url, src)
        if abs_src in seen:
            continue
        seen.add(abs_src)
        files.append({
            "src": src,
            "abs_src": abs_src,
            "alt": img.get("alt", "").strip(),
            "title": img.get("title", "").strip(),
        })
    return files


def extract_top_keywords(soup, url, top_n=25, min_score=50, candidate_pool=50):
    """Suggest other keywords worth targeting: scan the page's own text for
    1-3 word phrases, score each of the most frequent ones with the same
    engine used for the main check, and keep the ones scoring >= min_score."""
    corpus_text = soup.get_text(" ", strip=True)
    raw_words = re.findall(r"[A-Za-z][A-Za-z'-]*", corpus_text)
    words = [w for w in raw_words if len(w) > 1]

    counts = Counter()
    display = {}
    n = len(words)
    for i in range(n):
        for size in (1, 2, 3):
            if i + size > n:
                continue
            seq = words[i:i + size]
            if seq[0].lower() in STOP_WORDS or seq[-1].lower() in STOP_WORDS:
                continue
            key = " ".join(w.lower() for w in seq)
            counts[key] += 1
            display.setdefault(key, " ".join(seq))

    candidates = [k for k, c in counts.items() if c >= 2]
    candidates.sort(key=lambda k: -counts[k])
    candidates = candidates[:candidate_pool]

    scored = []
    for key in candidates:
        kw = display[key]
        _, total_score, total_max, _ = score_keyword(soup, kw, url)
        score_pct = pct(total_score, total_max)
        if score_pct >= min_score:
            scored.append({"keyword": kw, "score": score_pct})

    scored.sort(key=lambda x: -x["score"])
    return scored[:top_n]


def run_check(url, keyword, render=False):
    """Fetch the page and run all checks. Returns a structured dict.

    render=True loads the page in headless Chromium first, so content that a
    JS framework injects client-side (SPAs) is present before the checks run.
    """
    resp, elapsed = fetch_rendered(url) if render else fetch(url)
    soup = BeautifulSoup(resp.text, "lxml")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = desc_tag.get("content", "").strip() if desc_tag else ""
    robots_tag = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    robots = robots_tag.get("content", "").lower() if robots_tag else ""
    html_lang = soup.find("html").get("lang", "") if soup.find("html") else ""
    og_image_tag = soup.find("meta", attrs={"property": re.compile("^og:image$", re.I)})
    og_image = og_image_tag.get("content", "").strip() if og_image_tag else ""
    og_image = urljoin(url, og_image) if og_image else None

    headings_outline = extract_headings_outline(soup, keyword)
    text_blocks = extract_text_blocks(soup)
    media_files = extract_media_files(soup, url)

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    word_count = len(soup.get_text(" ", strip=True).split())

    top_keywords = extract_top_keywords(soup, url)

    group_summaries, total_score, total_max, issue_count = score_keyword(soup, keyword, url)

    return {
        "url": url,
        "keyword": keyword,
        "rendered": render,
        "overall_pct": pct(total_score, total_max),
        "overall_score": total_score,
        "overall_max": total_max,
        "issue_count": issue_count,
        "status_code": resp.status_code,
        "index": "noindex" if "noindex" in robots else "Index",
        "follow": "nofollow" if "nofollow" in robots else "Follow",
        "language": html_lang.upper() if html_lang else "n/a",
        "response_time": round(elapsed, 2),
        "file_size_kb": round(len(resp.content) / 1024, 2),
        "word_count": word_count,
        "title": title,
        "description": desc,
        "og_image": og_image,
        "groups": group_summaries,
        "headings_outline": headings_outline,
        "text_blocks": text_blocks,
        "media_files": media_files,
        "top_keywords": top_keywords,
    }
