"""
CMOA EdWeb Blog Scraper
-----------------------
Logs into the WordPress blog, then:
1. Downloads all .pdf and .docx files into /downloads
2. Saves each page as cleaned HTML into /pages
3. Collects YouTube and Vimeo links into video_links.xlsx

Resume-safe: tracks visited URLs and the crawl queue on disk
so you can kill and re-run without losing progress.
"""

import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook

# ── Configuration ──────────────────────────────────────────────────────────────

BASE_URL = "http://edweb.cmoa.org/"
LOGIN_URL = urljoin(BASE_URL, "/wp-login.php")
USERNAME = input("WordPress username: ")
PASSWORD = input("WordPress password: ")

OUTPUT_DIR = "cmoa_scrape"
DOWNLOAD_DIR = os.path.join(OUTPUT_DIR, "downloads")
PAGES_DIR = os.path.join(OUTPUT_DIR, "pages")
SPREADSHEET_PATH = os.path.join(OUTPUT_DIR, "video_links.xlsx")
VISITED_CACHE = os.path.join(OUTPUT_DIR, "visited_urls.txt")
QUEUE_CACHE = os.path.join(OUTPUT_DIR, "queue_urls.txt")

CRAWL_DELAY = 5
DOWNLOAD_EXTENSIONS = (".pdf", ".doc", ".docx")

VIDEO_PATTERNS = [
    re.compile(r"https?://(?:www\.)?youtube\.com/watch\?[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"https?://youtu\.be/[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?vimeo\.com/\d+[^\s\"'<>]*", re.IGNORECASE),
    re.compile(r"https?://player\.vimeo\.com/video/\d+[^\s\"'<>]*", re.IGNORECASE),
]

# ── Setup ──────────────────────────────────────────────────────────────────────

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(PAGES_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({"User-Agent": "CMOA-Docent-Scraper/1.0"})


# ── State management ──────────────────────────────────────────────────────────

def load_set_from_file(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_set_to_file(path, data):
    with open(path, "w") as f:
        for item in data:
            f.write(item + "\n")


def append_to_file(path, item):
    with open(path, "a") as f:
        f.write(item + "\n")


visited = load_set_from_file(VISITED_CACHE)
queue = load_set_from_file(QUEUE_CACHE) - visited

if visited:
    print(f"Loaded {len(visited)} previously visited URLs.")
if queue:
    print(f"Loaded {len(queue)} URLs still in queue.")

downloaded_files = []
video_links = []


# ── Login ──────────────────────────────────────────────────────────────────────

def login():
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def is_internal(url):
    parsed = urlparse(url)
    base_parsed = urlparse(BASE_URL)
    return parsed.netloc == "" or parsed.netloc == base_parsed.netloc


def clean_url(url):
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def extract_video_urls(soup, raw_html, page_url):
    found = set()
    for tag in soup.find_all(["a", "iframe", "embed", "source"]):
        for attr in ("href", "src", "data-src"):
            val = tag.get(attr, "")
            for pattern in VIDEO_PATTERNS:
                if pattern.search(val):
                    found.add(val.strip())
    for pattern in VIDEO_PATTERNS:
        for match in pattern.findall(raw_html):
            found.add(match.strip())
    for url in found:
        video_links.append((page_url, url))


def save_page_html(url, soup):
    safe_name = urlparse(url).path.strip("/").replace("/", "_") or "index"
    safe_name = re.sub(r'[<>:"|?*]', "_", safe_name)
    html_path = os.path.join(PAGES_DIR, f"{safe_name}.html")

    if os.path.exists(html_path):
        return

    content = soup.find("div", class_="entry-content") or soup.find("article") or soup.find("body")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: Georgia, serif; max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.6; }}
  img {{ max-width: 100%; height: auto; }}
  h1 {{ font-size: 1.5em; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p><small>Source: <a href="{url}">{url}</a></small></p>
<hr>
{content if content else '<p>No content found.</p>'}
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"    ✓ Saved page: {html_path}")


def download_file(url):
    filename = os.path.basename(urlparse(url).path)
    if not filename:
        return

    filepath = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(filepath):
        return

    try:
        resp = session.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        downloaded_files.append(url)
        print(f"    ✓ Downloaded: {filename}")
    except Exception as e:
        print(f"    ✗ Failed to download {filename}: {e}")


# ── Crawl ──────────────────────────────────────────────────────────────────────

def process_page(url):
    if url in visited:
        return
    print(f"  Crawling: {url}")

    try:
        resp = session.get(url, timeout=120)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            print(f"    ✗ Failed to fetch: 429 Too Many Requests — waiting {retry_after}s before retrying...")
            time.sleep(retry_after)
            resp = session.get(url, timeout=120)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            print(f"    ✗ Still rate-limited after retry, re-queuing: {url}")
            queue.add(url)
            save_set_to_file(QUEUE_CACHE, queue)
            time.sleep(120)
            return
        print(f"    ✗ Failed to fetch: {e}")
        visited.add(url)
        append_to_file(VISITED_CACHE, url)
        return
    except Exception as e:
        print(f"    ✗ Failed to fetch: {e}")
        visited.add(url)
        append_to_file(VISITED_CACHE, url)
        return

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" not in content_type:
        visited.add(url)
        append_to_file(VISITED_CACHE, url)
        return

    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup.find_all("a", href=True):
        href = clean_url(urljoin(url, tag["href"]))
        lower_href = href.lower()

        if any(lower_href.endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
            download_file(href)
        elif is_internal(href) and href not in visited and href not in queue:
            queue.add(href)

    for tag in soup.find_all(["embed", "object", "iframe"], src=True):
        src = urljoin(url, tag.get("src", ""))
        if any(src.lower().endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
            download_file(src)

    extract_video_urls(soup, resp.text, url)
    save_page_html(url, soup)

    visited.add(url)
    append_to_file(VISITED_CACHE, url)
    save_set_to_file(QUEUE_CACHE, queue)

    time.sleep(CRAWL_DELAY)


# ── Spreadsheet ────────────────────────────────────────────────────────────────

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

    if not queue:
        queue.add(BASE_URL)

    while queue:
        url = queue.pop()
        process_page(url)

    print(f"\n{'=' * 60}")
    print("Done!")
    print(f"  Pages crawled:    {len(visited)}")
    print(f"  Files downloaded: {len(downloaded_files)}")
    print(f"  Video links:      {len(video_links)}")
    print(f"{'=' * 60}")

    if video_links:
        write_spreadsheet()
    else:
        print("No video links found this run.")


if __name__ == "__main__":
    main()