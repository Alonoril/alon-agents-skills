#!/usr/bin/env python3
"""
Universal web content extractor (Scrapling + html2text).
Returns clean Markdown with headings, links, images, lists, and code blocks.

Usage:
  python3 fetch.py <url> [max_chars] [--stealth]
  python3 fetch.py <url> [max_chars] [--output-dir <dir>] [--output-name <name>]

Modes:
  (default)   Fast HTTP fetch via Fetcher — works for most sites (~1-3s)
  --stealth   Headless browser via StealthyFetcher — for JS-rendered or
              anti-scraping sites like WeChat, Zhihu, Juejin (~5-15s)

Examples:
  python3 fetch.py https://sspai.com/post/73145
  python3 fetch.py https://mp.weixin.qq.com/s/xxx 30000 --stealth
  python3 fetch.py https://zhuanlan.zhihu.com/p/12345 --stealth
"""

import sys
import re
import json
import logging
import argparse
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
from html import unescape


def check_dependencies():
    """Check if required packages are installed and provide install instructions."""
    missing = []
    try:
        import scrapling  # noqa: F401
    except ImportError:
        missing.append("scrapling")
    try:
        import html2text  # noqa: F401
    except ImportError:
        missing.append("html2text")

    if missing:
        print(
            f"Error: missing dependencies: {', '.join(missing)}\n"
            f"Install with:\n"
            f"  pip install {' '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)


def fix_lazy_images(html_raw):
    """
    Promote data-src to src for lazy-loaded images (WeChat, Zhihu, etc.).
    Many Chinese platforms use data-src for the real image URL while src
    holds a tiny placeholder. html2text only reads src, so we swap them.
    """
    return re.sub(
        r'<img([^>]*?)\sdata-src="([^"]+)"([^>]*?)>',
        lambda m: f'<img{m.group(1)} src="{m.group(2)}"{m.group(3)}>',
        html_raw,
    )


def normalize_img_src(html_raw):
    """
    Ensure img tags use real image URLs (prefer data-src/data-original over
    data:image placeholders) for stable markdown image links.
    """

    attr_re = re.compile(r'([:@\w-]+)\s*=\s*(".*?"|\'.*?\'|[^\s>]+)', re.DOTALL)
    src_re = re.compile(
        r'(?<![\w-])src\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)',
        re.IGNORECASE | re.DOTALL,
    )

    def _strip_quotes(value):
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        return value

    def _replace_img(match):
        tag = match.group(0)
        attrs = {}
        for key, raw in attr_re.findall(tag):
            attrs[key.lower()] = unescape(_strip_quotes(raw)).strip()

        preferred = ""
        for key in ("data-src", "data-original", "data-actualsrc", "data-lazy-src"):
            if attrs.get(key):
                preferred = attrs[key]
                break
        if not preferred:
            preferred = attrs.get("src", "")

        if not preferred:
            return tag

        preferred = preferred.replace('"', "&quot;")
        if src_re.search(tag):
            return src_re.sub(f'src="{preferred}"', tag, count=1)
        return tag[:-1] + f' src="{preferred}">'

    return re.sub(r"<img\b[^>]*>", _replace_img, html_raw, flags=re.IGNORECASE | re.DOTALL)


# CSS selectors in priority order — the first match with enough content wins.
# Covers most blog/article platforms without needing per-site customization.
CONTENT_SELECTORS = [
    "article",
    "main",
    ".post-content",
    ".entry-content",
    ".article-content",
    ".article-body",
    ".article-detail",         # 36kr
    ".article-holder",         # InfoQ
    ".post_body",              # 163.com (NetEase)
    ".markdown-body",          # GitHub
    ".Post-RichText",          # Zhihu
    "#article_content",        # CSDN
    ".article-area",           # Juejin
    ".ssa-article",            # Toutiao
    '[role="article"]',
    '[itemprop="articleBody"]',
]

# WeChat has a unique DOM structure — try these first for mp.weixin.qq.com
WECHAT_SELECTORS = [
    "div#js_content",
    "div.rich_media_content",
]

# Minimum characters for a selector match to be considered "real content"
MIN_CONTENT_LENGTH = 200


def html_to_markdown(html_raw, max_chars=30000):
    """Convert raw HTML to clean Markdown."""
    import html2text

    html_raw = normalize_img_src(html_raw)

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0       # No line wrapping
    h.skip_internal_links = True
    h.ignore_emphasis = False

    md = h.handle(html_raw)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md[:max_chars]


def extract_content(page, url, max_chars=30000):
    """
    Try content selectors to find the article body.
    Returns (markdown_text, matched_selector).
    """
    is_wechat = "mp.weixin.qq.com" in url
    selectors = (WECHAT_SELECTORS + CONTENT_SELECTORS) if is_wechat else CONTENT_SELECTORS

    for selector in selectors:
        els = page.css(selector)
        if els:
            md = html_to_markdown(els[0].html_content, max_chars)
            if len(md) >= MIN_CONTENT_LENGTH:
                return md, selector

    # Fallback: convert the entire page
    md = html_to_markdown(page.html_content, max_chars)
    return md, "body(fallback)"


def _suppress_scrapling_logs():
    """Scrapling's logger is noisy (deprecation warnings, fetch info). Silence it."""
    logging.getLogger("scrapling").setLevel(logging.CRITICAL)


def fetch_fast(url, max_chars=30000, timeout=15):
    """
    Fast HTTP fetch — no JavaScript execution.
    Works for most blogs and static sites.
    """
    from scrapling.fetchers import Fetcher
    _suppress_scrapling_logs()

    page = Fetcher().get(url, timeout=timeout, stealthy_headers=True)
    md, selector = extract_content(page, url, max_chars)
    metadata = extract_metadata(page, url)
    return md, selector, metadata


def fetch_stealth(url, max_chars=30000, timeout=30000):
    """
    Headless browser fetch — executes JavaScript, bypasses anti-scraping.
    Required for: WeChat articles, Zhihu, Juejin, and other JS-rendered pages.
    Slower (~5-15s) but more reliable for protected content.
    """
    from scrapling.fetchers import StealthyFetcher
    _suppress_scrapling_logs()

    page = StealthyFetcher().fetch(
        url,
        headless=True,
        network_idle=True,
        timeout=timeout,
    )
    md, selector = extract_content(page, url, max_chars)
    metadata = extract_metadata(page, url)
    return md, selector, metadata


def fetch(url, max_chars=30000, stealth=False):
    """
    Main entry point. Fetches URL and returns (markdown, selector, mode, metadata).
    If stealth=False, tries fast mode first and falls back to stealth
    when the result is too short (likely a JS-rendered page).
    """
    if stealth:
        md, selector, metadata = fetch_stealth(url, max_chars)
        return md, selector, "stealth", metadata

    # Try fast mode first
    md, selector, metadata = fetch_fast(url, max_chars)

    # If fast mode got barely any content, the page likely needs JS rendering
    if len(md) < MIN_CONTENT_LENGTH:
        try:
            md_stealth, sel_stealth, meta_stealth = fetch_stealth(url, max_chars)
            if len(md_stealth) > len(md):
                return md_stealth, sel_stealth, "stealth(auto-fallback)", meta_stealth
        except Exception:
            pass  # Stick with fast mode result

    return md, selector, "fast", metadata


def clean_text(value):
    """Normalize text by collapsing whitespace."""
    return re.sub(r"\s+", " ", (value or "")).strip()


def yaml_escape(value):
    """Escape a value for safe double-quoted YAML."""
    text = clean_text(str(value or ""))
    return text.replace("\\", "\\\\").replace('"', '\\"')


def first_text(page, selectors):
    """Return the first non-empty text for any selector."""
    for selector in selectors:
        elements = page.css(selector)
        for element in elements:
            text = clean_text(getattr(element, "text", ""))
            if text:
                return text
    return ""


def first_attr(page, selectors, attr):
    """Return the first non-empty attribute value for any selector."""
    for selector in selectors:
        elements = page.css(selector)
        for element in elements:
            value = clean_text(element.attrib.get(attr))
            if value:
                return value
    return ""


def normalize_date(value):
    """Convert date strings to YYYY-MM-DD when possible."""
    text = clean_text(value)
    if not text:
        return ""

    m = re.search(r"(\d{4})[年/.\-](\d{1,2})[月/.\-](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        pass

    m = re.search(r"(\d{4})(\d{2})(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return ""


def strip_leading_title_heading(md, title):
    """Avoid duplicated title heading in output."""
    content = (md or "").strip()
    if not content:
        return content

    lines = content.splitlines()
    if not lines:
        return content

    first = lines[0].strip()
    m = re.match(r"^#{1,6}\s+(.+)$", first)
    if m and clean_text(m.group(1)) == clean_text(title):
        return "\n".join(lines[1:]).strip()
    return content


def extract_metadata(page, url):
    """Extract title/source/account/author/published/created/tags metadata."""
    host = urlparse(url).netloc.lower()
    is_wechat = "mp.weixin.qq.com" in host

    title = first_text(
        page,
        [
            "h1#activity-name .js_title_inner",
            "h1#activity-name",
            "h1.rich_media_title .js_title_inner",
            "h1.rich_media_title",
            "title",
        ],
    )
    if not title:
        title = first_attr(page, ['meta[property="og:title"]'], "content")
    if not title:
        title = "Untitled"

    source = first_attr(page, ['meta[property="og:url"]', 'link[rel="canonical"]'], "content")
    if not source:
        source = first_attr(page, ['link[rel="canonical"]'], "href")
    if not source:
        source = url

    account = ""
    if is_wechat:
        account = first_text(page, ["#js_name", "a#js_name"])

    author = first_text(page, ["#js_author_name"])
    if not author:
        author = first_attr(page, ['meta[name="author"]'], "content")

    published_raw = first_text(page, ["#publish_time", "em#publish_time"])
    if not published_raw:
        published_raw = first_attr(
            page,
            [
                'meta[property="article:published_time"]',
                'meta[name="publishdate"]',
            ],
            "content",
        )
    published = normalize_date(published_raw)
    created = datetime.now().date().isoformat()
    if not published:
        published = created

    tags = ["clipped"]
    if is_wechat:
        tags.insert(0, "微信公众号")
    elif host:
        tags.insert(0, host.removeprefix("www."))

    return {
        "title": title,
        "source": source,
        "account": account,
        "author": author,
        "published": published,
        "created": created,
        "tags": tags,
    }


def build_document(metadata, body_md):
    """Build final markdown document with YAML front matter."""
    title = metadata.get("title") or "Untitled"
    source = metadata.get("source") or ""
    account = metadata.get("account") or ""
    author = metadata.get("author") or ""
    published = metadata.get("published") or datetime.now().date().isoformat()
    created = metadata.get("created") or datetime.now().date().isoformat()
    tags = metadata.get("tags") or ["clipped"]

    body = strip_leading_title_heading(body_md, title)

    lines = [
        "---",
        f'title: "{yaml_escape(title)}"',
        f'source: "<{yaml_escape(source)}>"',
        f'account: "{yaml_escape(account)}"',
        f'author: "{yaml_escape(author)}"',
        f'published: "{yaml_escape(published)}"',
        f'created: "{yaml_escape(created)}"',
        "tags:",
    ]
    for tag in tags:
        cleaned = clean_text(tag)
        if cleaned:
            lines.append(f"  - {cleaned}")
    lines.extend(["---", "", f"# {title}", ""])
    if body:
        lines.append(body)
    return "\n".join(lines)


def make_output_path(url, output_dir, output_name, json_output=False, title=None):
    """Build output path under output_dir, with a safe default filename."""
    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if output_name:
        filename = Path(output_name).name
    else:
        filename = clean_text(title) or ""
        if not filename:
            parsed = urlparse(url)
            host = parsed.netloc or "page"
            tail = parsed.path.rstrip("/").split("/")[-1] if parsed.path else "index"
            tail = tail or "index"
            tail = tail.split(".")[0] if "." in tail else tail
            filename = f"{host}_{tail}"

    filename = clean_text(filename)
    filename = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", filename).strip(" ._")
    if not filename:
        filename = "content"

    default_ext = ".json" if json_output else ".md"
    if not Path(filename).suffix:
        filename += default_ext

    return out_dir / filename


def main():
    parser = argparse.ArgumentParser(
        description="Fetch a web page and extract main content as Markdown."
    )
    parser.add_argument("url", help="Target URL")
    parser.add_argument(
        "max_chars",
        nargs="?",
        type=int,
        default=30000,
        help="Maximum output characters (default: 30000)",
    )
    parser.add_argument(
        "--stealth",
        action="store_true",
        help="Use headless browser mode for JS-rendered pages",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output JSON with metadata",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory to save output file (.md or .json)",
    )
    parser.add_argument(
        "--output-name",
        type=str,
        help="Optional output file name (without path). Implies file output.",
    )
    args = parser.parse_args()

    try:
        md, selector, mode, metadata = fetch(args.url, args.max_chars, stealth=args.stealth)
        document = build_document(metadata, md)

        result = {
            "url": args.url,
            "mode": mode,
            "selector": selector,
            "content_length": len(document),
            "content": document,
            "metadata": metadata,
        }

        output_text = (
            json.dumps(result, ensure_ascii=False, indent=2)
            if args.json_output
            else document
        )

        wants_file_output = bool(args.output_dir or args.output_name)
        if wants_file_output:
            output_dir = args.output_dir or "."
            output_path = make_output_path(
                args.url,
                output_dir,
                args.output_name,
                json_output=args.json_output,
                title=metadata.get("title"),
            )
            if args.json_output:
                result["output_path"] = str(output_path)
                output_text = json.dumps(result, ensure_ascii=False, indent=2)
            output_path.write_text(output_text, encoding="utf-8")
            print(f"Saved to: {output_path}", file=sys.stderr)
        else:
            print(output_text)

    except Exception as e:
        error_msg = f"Error fetching {args.url}: {type(e).__name__}: {e}"
        if args.json_output:
            print(json.dumps({"url": args.url, "error": error_msg}, ensure_ascii=False))
        else:
            print(error_msg, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    check_dependencies()
    main()
