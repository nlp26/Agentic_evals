#!/usr/bin/env python3
import os
import re
import json
import time
import random
import asyncio
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext, APIRequestContext

load_dotenv()

# --- Configuration ---
# Base config (Zendesk Help Center)
BASE_ORIGIN = os.getenv("BASE_ORIGIN", "https://help.viantinc.com")
HELP_CENTER_LOCALE = os.getenv("HELP_CENTER_LOCALE", "en-us")

# API base (Zendesk Guide/Help Center)
API_BASE = f"{BASE_ORIGIN}/api/v2/help_center/{HELP_CENTER_LOCALE}"

# Output and runtime config
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "help_articles.json")
MAX_URLS = int(os.getenv("MAX_URLS", "0"))  # 0 => no limit
SCRAPE_DELAY = float(os.getenv("SCRAPE_DELAY", "0.15"))

# Rate limit/backoff
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "6"))
BACKOFF_BASE_MS = int(os.getenv("BACKOFF_BASE_MS", "1000"))
BACKOFF_MAX_MS = int(os.getenv("BACKOFF_MAX_MS", "30000"))

# CDP (reuse your logged-in Chrome session)
CDP_ENDPOINT = os.getenv("CDP_ENDPOINT", "http://127.0.0.1:9222")

# Regex for article links (used to extract in-body references)
LOCALE_RE = r"[a-z]{2}-[a-z]{2}"
ARTICLE_RE = re.compile(rf"/hc/{LOCALE_RE}/articles/\d+")

# --- Helper Functions ---
def abs_url(base: str, href: str) -> str:
    if not href:
        return href
    return urljoin(base, href)

def normalize_image_srcs(container, page_url: str, origin: str):
    if not container:
        return
    for img in container.find_all("img"):
        src = img.get("src") or ""
        if src.startswith("//"):
            parsed = urlparse(page_url)
            img["src"] = f"{parsed.scheme}:{src}"
        elif src.startswith("/"):
            img["src"] = urljoin(origin, src)

def clean_text_for_vectors(text: str) -> str:
    if not text:
        return ""
    txt = text.replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()

def parse_body(body_html: str, html_url: str) -> Tuple[str, str, List[Dict], List[str]]:
    images = []
    in_body_links = []
    if not body_html:
        return "", "", images, in_body_links

    soup = BeautifulSoup(body_html, "html.parser")
    normalize_image_srcs(soup, html_url, BASE_ORIGIN)

    # Collect images
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            images.append({"src": src, "alt": img.get("alt", "")})

    # Collect in-body article links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ARTICLE_RE.search(href):
            in_body_links.append(abs_url(BASE_ORIGIN, href))

    body_html_norm = str(soup)
    body_text = clean_text_for_vectors(soup.get_text("\n", strip=True))

    return body_html_norm, body_text, images, sorted(set(in_body_links))

async def exp_backoff_sleep(attempt: int) -> float:
    base = BACKOFF_BASE_MS / 1000.0
    max_s = BACKOFF_MAX_MS / 1000.0
    sleep = min(max_s, base * (2 ** (attempt - 1)))
    return sleep * random.uniform(0.5, 1.5)

# --- API interaction ---
async def api_get_json(request_ctx: APIRequestContext, url: str) -> Optional[Dict]:
    attempts = 0
    last_error = None

    while attempts < RETRY_ATTEMPTS:
        attempts += 1
        try:
            resp = await request_ctx.get(url, headers={"Accept": "application/json"})
            status = resp.status

            if status == 429 or status == 503:
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else await exp_backoff_sleep(attempts)
                await asyncio.sleep(wait)
                continue

            if status >= 400:
                last_error = f"HTTP {status}"
                wait = await exp_backoff_sleep(attempts)
                await asyncio.sleep(wait)
                continue

            data = await resp.json()
            await asyncio.sleep(SCRAPE_DELAY)
            return data

        except Exception as e:
            last_error = str(e)
            wait = await exp_backoff_sleep(attempts)
            await asyncio.sleep(wait)

    print(f"API GET failed for {url}: {last_error}")
    return None

async def connect_request_context(p) -> Tuple[BrowserContext, APIRequestContext]:
    # Attach to your logged-in Chrome via CDP and clone storage into a request context
    browser: Browser = await p.chromium.connect_over_cdp(CDP_ENDPOINT)
    if not browser.contexts:
        raise RuntimeError("No contexts found in attached Chrome. Open any tab and try again.")
    context = browser.contexts[0]
    storage = await context.storage_state()
    req_ctx = await p.request.new_context(base_url=BASE_ORIGIN, storage_state=storage)
    return context, req_ctx

# --- Data Fetching ---
async def fetch_all_categories(req: APIRequestContext) -> Dict[int, Dict]:
    categories: Dict[int, Dict] = {}
    url = f"{API_BASE}/categories.json?per_page=100&page=1"

    while url:
        data = await api_get_json(req, url)
        if not data:
            break

        for cat in data.get("categories", []):
            categories[cat["id"]] = cat
        url = data.get("next_page")

    return categories

async def fetch_all_sections(req: APIRequestContext) -> Dict[int, Dict]:
    sections: Dict[int, Dict] = {}
    url = f"{API_BASE}/sections.json?per_page=100&page=1"

    while url:
        data = await api_get_json(req, url)
        if not data:
            break

        for sec in data.get("sections", []):
            sections[sec["id"]] = sec
        url = data.get("next_page")

    return sections

async def fetch_all_articles(req: APIRequestContext, max_urls: int = 0) -> List[Dict]:
    articles: List[Dict] = []
    url = f"{API_BASE}/articles.json?per_page=100&page=1"

    while url:
        data = await api_get_json(req, url)
        if not data:
            break

        batch = data.get("articles", [])
        articles.extend(batch)

        if max_urls and len(articles) >= max_urls:
            articles = articles[:max_urls]
            break
        url = data.get("next_page")

    return articles

# --- Data Transformation ---
def build_output_record(article: Dict, sections: Dict[int, Dict], categories: Dict[int, Dict]) -> Dict:
    html_url = article.get("html_url") or ""
    title = article.get("title") or ""
    body_html_src = article.get("body") or ""  # HTML from API
    body_html, body_text, images, in_body_links = parse_body(body_html_src, html_url)

    section_id = article.get("section_id")
    section_name = sections.get(section_id, {}).get("name") if section_id else None
    category_id = sections.get(section_id, {}).get("category_id") if section_id in sections else None
    category_name = categories.get(category_id, {}).get("name") if category_id in categories else None

    return {
        "id": article.get("id"),
        "url": html_url,
        "title": title,
        "body_text": body_text,
        "body_html": body_html,
        "images": images,
        "in_body_article_links": in_body_links,
        "section_id": section_id,
        "section_name": section_name,
        "category_id": category_id,
        "category_name": category_name,
        "draft": article.get("draft"),
        "outdated": article.get("outdated"),
        "updated_at": article.get("updated_at"),
        "created_at": article.get("created_at"),
        "author_id": article.get("author_id"),
        "comments_disabled": article.get("comments_disabled"),
        "label_names": article.get("label_names") or [],
        "promoted": article.get("promoted"),
        "permission_group_id": article.get("permission_group_id"),
        "locale": article.get("locale"),
        "source_locale": article.get("source_locale"),
    }

# --- Main Execution ---
async def main():
    async with async_playwright() as p:
        context, req = await connect_request_context(p)

        # 1. Fetch metadata
        categories = await fetch_all_categories(req)
        sections = await fetch_all_sections(req)

        # 2. Fetch articles
        raw_articles = await fetch_all_articles(req, max_urls=MAX_URLS)

        # 3. Transform
        out: List[Dict] = []
        for art in raw_articles:
            rec = build_output_record(art, sections, categories)
            out.append(rec)

        # 4. Save
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        print(f"Wrote {len(out)} articles to {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
