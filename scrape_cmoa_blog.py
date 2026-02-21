"""
CMOA EdWeb Blog Scraper
-----------------------
Logs into the WordPress blog, then:
1. Downloads all .pdf and .docx files into /downloads
2. Renders every page/post as a PDF into /pages
3. Collects YouTube and Vimeo links into video_links.xlsx
"""

import os
import re
import time
from urllib.parse import urljoin, urlparse

import pdfkit
import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook

# ── Configuration ──────────────────────────────────────────────────────────────

BASE_URL = "http://edweb.cmoa.org/"
LOGIN_URL = urljoin(BASE_URL, "/wp-login.php")
USERNAME = input("WordPress username: ")
PASSWORD = input("WordPress password: ")

OUTPUT_DIR = "cmoa_scrape"
DOWNLOAD_DIR = os.path.join(OUTPUT_DIR, "downloads")   # PDFs and Word docs
PAGES_DIR = os.path.join(OUTPUT_DIR, "pages")           # rendered page PDFs
SPREADSHEET_PATH = os.path.join(OUTPUT_DIR, "video_links.xlsx")

CRAWL_DELAY = 1  # seconds between requests, be polite

# File extensions to download
DOWNLOAD_EXTENSIONS = (".pdf", ".doc", ".docx")

# Regex patterns for video URLs
VIDEO_PATTERNS = [
    re.compile(r"https?://(?:www\.)?youtube\.com/watch\?[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"https?://youtu\.be/[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?vimeo\.com/\d+[^\s\"'<>]*", re.IGNORECASE),
    re.compile(r"https?://player\.vimeo\.com/video/\d+[^\s\"'<>]*", re.IGNORECASE),
]

# pdfkit options
PDFKIT_OPTIONS = {
    "quiet": "",
    "no-images": "",           # skip images to avoid broken-image issues
    "disable-javascript": "",
    "encoding": "UTF-8",
}

# ── Setup ──────────────────────────────────────────────────────────────────────

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(PAGES_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({"User-Agent": "CMOA-Docent-Scraper/1.0"})


# ── Login ──────────────────────────────────────────────────────────────────────

def login():
    # Get the login page first (picks up cookies)
    session.get(LOGIN_URL)
    payload = {
        "log": USERNAME,
        "pwd": PASSWORD,
        "wp-submit": "Log In",
        "redirect_to": BASE_URL,
        "testcookie": "1",
    }
    resp = session.post(LOGIN_URL, data=payload, allow_redirects=True)
    if "wordpress_logged_in" in str(session.cookies) or resp.url != LOGIN_URL:
        print("✓ Logged in successfully.")
    else:
        print("✗ Login may have failed. Check credentials. Continuing anyway...")


# ── Crawling ───────────────────────────────────────────────────────────────────

visited = set()
to_visit = set()
downloaded_files = []
video_links = []  # list of (page_url, video_url)


def is_internal(url):
    parsed = urlparse(url)
    base_parsed = urlparse(BASE_URL)
    return parsed.netloc == "" or parsed.netloc == base_parsed.netloc


def clean_url(url):
    """Remove fragment, keep query string."""
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def extract_video_urls(soup, raw_html, page_url):
    """Find YouTube/Vimeo links in both parsed HTML and raw source."""
    found = set()

    # Check href and src attributes
    for tag in soup.find_all(["a", "iframe", "embed", "source"]):
        for attr in ("href", "src", "data-src"):
            val = tag.get(attr, "")
            for pattern in VIDEO_PATTERNS:
                if pattern.search(val):
                    found.add(val.strip())

    # Also regex the raw HTML for URLs that might not be in tags
    for pattern in VIDEO_PATTERNS:
        for match in pattern.findall(raw_html):
            found.add(match.strip())

    for url in found:
        video_links.append((page_url, url))


def process_page(url):
    """Download the page, extract links/files/videos, render to PDF."""
    if url in visited:
        return
    visited.add(url)
    print(f"  Crawling: {url}")

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"    ✗ Failed to fetch: {e}")
        return

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" not in content_type:
        return

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Extract links ──────────────────────────────────────────────────────
    for tag in soup.find_all("a", href=True):
        href = urljoin(url, tag["href"])
        href = clean_url(href)

        # Check if it's a downloadable file
        lower_href = href.lower()
        if any(lower_href.endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
            download_file(href)
            continue

        # Queue internal links for crawling
        if is_internal(href) and href not in visited:
            to_visit.add(href)

    # Also check for files linked via non-anchor tags (e.g. embedded objects)
    for tag in soup.find_all(["embed", "object", "iframe"], src=True):
        src = urljoin(url, tag.get("src", ""))
        if any(src.lower().endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
            download_file(src)

    # ── Extract video links ────────────────────────────────────────────────
    extract_video_urls(soup, resp.text, url)

    # ── Render page to PDF ─────────────────────────────────────────────────
    try:
        safe_name = urlparse(url).path.strip("/").replace("/", "_") or "index"
        safe_name = re.sub(r'[<>:"|?*]', "_", safe_name)
        pdf_path = os.path.join(PAGES_DIR, f"{safe_name}.pdf")

        # Pass cookies so pdfkit can access protected pages
        cookie_str = "; ".join(
            f"{c.name}={c.value}" for c in session.cookies
        )
        options = {**PDFKIT_OPTIONS, "cookie": []}
        # pdfkit wants cookies as repeated --cookie name value pairs
        pdfkit_cookies = []
        for c in session.cookies:
            pdfkit_cookies.extend(["--cookie", c.name, c.value])

        # Use from_url with cookies passed via command-line config
        config_options = {**PDFKIT_OPTIONS}
        # Build cookie options properly for pdfkit
        cookie_list = [(c.name, c.value) for c in session.cookies]

        pdfkit.from_url(
            url,
            pdf_path,
            options={
                **PDFKIT_OPTIONS,
                **{f"cookie {name}": value for name, value in cookie_list},
            },
        )
        print(f"    ✓ Saved page PDF: {pdf_path}")
    except Exception as e:
        print(f"    ✗ Could not render PDF for {url}: {e}")

    time.sleep(CRAWL_DELAY)


def download_file(url):
    """Download a file to the downloads directory."""
    if url in downloaded_files:
        return
    downloaded_files.append(url)

    filename = os.path.basename(urlparse(url).path)
    if not filename:
        return

    filepath = os.path.join(DOWNLOAD_DIR, filename)

    # Handle duplicate filenames
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(filepath):
            filepath = os.path.join(DOWNLOAD_DIR, f"{base}_{counter}{ext}")
            counter += 1

    try:
        resp = session.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"    ✓ Downloaded: {filename}")
    except Exception as e:
        print(f"    ✗ Failed to download {filename}: {e}")


# ── Write video links spreadsheet ─────────────────────────────────────────────

def write_spreadsheet():
    wb = Workbook()
    ws = wb.active
    ws.title = "Video Links"
    ws.append(["Source Page", "Video URL", "Platform"])

    for page_url, vid_url in video_links:
        if "vimeo" in vid_url.lower():
            platform = "Vimeo"
        elif "youtube" in vid_url.lower() or "youtu.be" in vid_url.lower():
            platform = "YouTube"
        else:
            platform = "Unknown"
        ws.append([page_url, vid_url, platform])

    # Auto-size columns roughly
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 80)

    wb.save(SPREADSHEET_PATH)
    print(f"\n✓ Video links spreadsheet saved: {SPREADSHEET_PATH}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("CMOA EdWeb Blog Scraper")
    print("=" * 60)

    login()

    # Seed the crawl with the homepage
    to_visit.add(BASE_URL)

    while to_visit:
        url = to_visit.pop()
        process_page(url)

    print(f"\n{'=' * 60}")
    print(f"Done!")
    print(f"  Pages crawled:    {len(visited)}")
    print(f"  Files downloaded: {len(downloaded_files)}")
    print(f"  Video links:      {len(video_links)}")
    print(f"{'=' * 60}")

    write_spreadsheet()


if __name__ == "__main__":
    main()
