"""
On-page keyword SEO checker (Seobility-style), for a single URL + target keyword.

Usage:
    python seo_keyword_check.py "https://example.com/page" "keyword" [--render]

--render loads the page in headless Chromium first, for JS-rendered / SPA pages
whose content isn't present in the raw HTML response.
"""
import re
import sys

from seo_checker.core import run_check

ISSUE_MARKER = {"good": "+", "warn": "!", "bad": "x", "info": "-"}


def plain_text(html):
    return re.sub(r"<[^>]+>", "", html)


def print_report(result):
    print("=" * 70)
    print(f"Keyword score: {result['overall_pct']}%  ({result['overall_score']}/{result['overall_max']})")
    print("=" * 70)
    print(f"Page URL      : {result['url']}")
    print(f"Status code   : {result['status_code']}")
    print(f"Page status   : {result['index']}, {result['follow']}")
    print(f"Language      : {result['language']}")
    print(f"Response time : {result['response_time']} sec")
    print(f"File size     : {result['file_size_kb']} kB")
    print(f"Word count    : {result['word_count']}")
    print(f"Meta title    : {result['title']}")
    print(f"Meta descr.   : {result['description']}")
    if result["rendered"]:
        print("(fetched with headless-browser JS rendering)")
    print()

    for label, group in result["groups"].items():
        print(f"{label}  --  {group['pct']}%")
        print("-" * 70)
        for c in group["checks"]:
            status = "OK" if c["score"] == c["max"] else ("WARNING" if c["score"] > 0 else "ERROR")
            print(f"  [{status:7}] {c['name']} ({c['score']}/{c['max']}, {c['importance']})")
            for entry in c["issues"]:
                marker = ISSUE_MARKER.get(entry["level"], "-")
                print(f"      [{marker}] {plain_text(entry['text'])}")
        print()


def main():
    args = [a for a in sys.argv[1:] if a != "--render"]
    render = "--render" in sys.argv[1:]
    if len(args) != 2:
        print('Usage: python seo_keyword_check.py "https://example.com/page" "keyword" [--render]')
        sys.exit(1)
    url, keyword = args
    result = run_check(url, keyword, render=render)
    print_report(result)


if __name__ == "__main__":
    main()
