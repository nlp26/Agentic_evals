#!/usr/bin/env python3
import os
import re
import json
import asyncio
from typing import List, Set, Dict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

BASE_ORIGIN = os.getenv("BASE_ORIGIN", "https://help.viantinc.com")
HELP_CENTER_LOCALE = os.getenv("HELP_CENTER_LOCALE", "en-us")
HC_BASE = f"{BASE_ORIGIN}/hc/{HELP_CENTER_LOCALE}"

INDEX_URL = f"{HC_BASE}"
ARTICLES_INDEX_URL = f"{HC_BASE}/articles"
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "help_articles.json")
DELAY = float(os.getenv("SCRAPE_DELAY", "0.2"))
MAX_URLS = int(os.getenv("MAX_URLS", "0"))  # 0 means no limit
CDP_ENDPOINT = os.getenv("CDP_ENDPOINT", "http://127.0.0.1:9222")  # Chrome DevTools endpoint

# Regex for structures commonly used by Zendesk Help Center
CATEGORY_RE = re.compile(r"/hc/[a-z]{2}-[a-z]{2}/categories/\d+")
SECTION_RE = re.compile(r"/hc/[a-z]{2}-[a-z]{2}/sections/\d+")
ARTICLE_RE = re.compile(r"/hc/[a-z]{2}-[a-z]{2}/articles/\d+")

# Selectors for content extraction (cover common Zendesk themes)
TITLE_SELECTORS = [
    "h1.article-title__title",
    "h1.article__title",
    "h1.article-title",
    "h1",
]
BODY_SELECTORS = [
    ".article-body",
    ".article-content",
    "article.article",
    "article",
]
RELATED_SELECTORS = [
    ".related-articles",
    ".article-related",
    ".article-relatives",
]

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
            # Protocol-relative: //cdn...
            parsed = urlparse(page_url)
            img["src"] = f"{parsed.scheme}:{src}"
        elif src.startswith("/"):
            img["src"] = urljoin(origin, src)

def extract_text(el) -> str:
    return el.get_text(strip=True) if el else ""

def unique_keep_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

async def connect_chrome_over_cdp(p):
    try:
        browser = await p.chromium.connect_over_cdp(CDP_ENDPOINT)
        return browser
    except Exception as e:
        print("Failed to connect to Chrome over CDP at", CDP_ENDPOINT)
        print("Start Chrome with: --remote-debugging-port=9222")
        print("Error:", e)
        return None

async def get_default_context_and_page(browser):
    # Use existing context (your real Chrome profile)
    contexts = browser.contexts
    if contexts:
        context = contexts[0]
    else:
        # Should rarely happen; create one if needed
        context = await browser.new_context()
    page = await context.new_page()
    return context, page

async def eval_all_links(page) -> List[str]:
    try:
        urls = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        return urls or []
    except Exception:
        return []

async def collect_categories_and_sections(page) -> Dict[str, List[str]]:
    categories: Set[str] = set()
    sections: Set[str] = set()

    # 1) Home page often lists categories
    for url in [INDEX_URL, ARTICLES_INDEX_URL]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"Navigate error {url}: {e}")
            continue

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Collect via soup
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if CATEGORY_RE.search(href):
                categories.add(abs_url(BASE_ORIGIN, href))
            if SECTION_RE.search(href):
                sections.add(abs_url(BASE_ORIGIN, href))

        # Collect via DOM (covers dynamic rendering)
        for href in await eval_all_links(page):
            if CATEGORY_RE.search(href):
                categories.add(href)
            if SECTION_RE.search(href):
                sections.add(href)

    return {
        "categories": sorted(categories),
        "sections": sorted(sections),
    }

async def collect_sections_from_category(page, category_url: str) -> List[str]:
    found: Set[str] = set()
    try:
        await page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.2)
    except Exception as e:
        print(f"Category navigate error {category_url}: {e}")
        return []

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if SECTION_RE.search(href):
            found.add(abs_url(BASE_ORIGIN, href))
    for href in await eval_all_links(page):
        if SECTION_RE.search(href):
            found.add(href)
    return sorted(found)

async def collect_articles_from_section(page, section_url: str) -> List[str]:
    found: Set[str] = set()
    next_url = section_url
    hops = 0
    while next_url and hops < 50:
        hops += 1
        try:
            await page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.2)
        except Exception as e:
            print(f"Section navigate error {next_url}: {e}")
            break

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Gather article links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ARTICLE_RE.search(href):
                found.add(abs_url(BASE_ORIGIN, href))
        for href in await eval_all_links(page):
            if ARTICLE_RE.search(href):
                found.add(href)

        # Find pagination next
        next_link = None
        rel_next = soup.find("a", rel="next")
        if rel_next and rel_next.get("href"):
            next_link = rel_next["href"].strip()
        if not next_link:
            txt_next = soup.find("a", string=re.compile(r"\bNext\b", re.I))
            if txt_next and txt_next.get("href"):
                next_link = txt_next["href"].strip()

        if next_link:
            next_url = abs_url(BASE_ORIGIN, next_link)
            if next_url == section_url:
                break
        else:
            next_url = None

        await asyncio.sleep(DELAY)

    return sorted(found)

async def scrape_article(page, url: str) -> Dict:
    print(f"Scraping article: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await asyncio.sleep(1.5)
    except PlaywrightTimeout:
        print(f"Timeout: {url}")
        return {}
    except Exception as e:
        print(f"Navigate error {url}: {e}")
        return {}

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    for sel in TITLE_SELECTORS:
        el = soup.select_one(sel)
        if el:
            title = extract_text(el)
            if title:
                break

    # Body (both text and HTML, with absolute image URLs)
    body_html = ""
    body_text = ""
    body_container = None
    for sel in BODY_SELECTORS:
        body_container = soup.select_one(sel)
        if body_container:
            normalize_image_srcs(body_container, url, BASE_ORIGIN)
            body_html = str(body_container)
            body_text = body_container.get_text("\n", strip=True)
            break

    # Images (from body container)
    images = []
    if body_container:
        for img in body_container.find_all("img"):
            src = img.get("src")
            alt = img.get("alt", "")
            if src:
                images.append({"src": src, "alt": alt})

    # Related/Sub-articles
    subarticles = []
    for sel in RELATED_SELECTORS:
        rel = soup.select_one(sel)
        if not rel:
            continue
        for a in rel.find_all("a", href=True):
            href = a["href"]
            if ARTICLE_RE.search(href):
                subarticles.append({
                    "title": extract_text(a),
                    "url": abs_url(BASE_ORIGIN, href)
                })
        if subarticles:
            break

    return {
        "url": url,
        "title": title,
        "body_text": body_text,
        "body_html": body_html,
        "images": images,
        "subarticles": unique_keep_order(subarticles),
    }

async def main():
    async with async_playwright() as p:
        browser = await connect_chrome_over_cdp(p)
        if not browser:
            return

        context, page = await get_default_context_and_page(browser)

        # Discover structure: categories, sections
        structure = await collect_categories_and_sections(page)
        categories = structure["categories"]
        sections = set(structure["sections"])

        # Add sections discovered via categories
        for cat in categories:
            secs = await collect_sections_from_category(page, cat)
            for s in secs:
                sections.add(s)
            await asyncio.sleep(DELAY)

        # Collect articles from sections
        article_urls: Set[str] = set()
        for idx, sec in enumerate(sorted(sections), start=1):
            print(f"[Sections] {idx}/{len(sections)} -> {sec}")
            arts = await collect_articles_from_section(page, sec)
            for a in arts:
                article_urls.add(a)
            await asyncio.sleep(DELAY)

        # Fallback: also scan ARTICLES_INDEX_URL directly for any stray article links
        try:
            await page.goto(ARTICLES_INDEX_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if ARTICLE_RE.search(href):
                    article_urls.add(abs_url(BASE_ORIGIN, href))
            for href in await eval_all_links(page):
                if ARTICLE_RE.search(href):
                    article_urls.add(href)
        except Exception:
            pass

        urls = sorted(article_urls)
        if not urls:
            print("No article URLs found. Make sure Chrome is started with --remote-debugging-port=9222 and that your session can access the Help Center.")
            return

        if MAX_URLS and len(urls) > MAX_URLS:
            print(f"Truncating to first {MAX_URLS} URLs for this run.")
            urls = urls[:MAX_URLS]

        print(f"Discovered {len(urls)} article URLs. Beginning scrape...")

        articles = []
        total = len(urls)
        for i, url in enumerate(urls, start=1):
            print(f"[{i}/{total}] {url}")
            data = await scrape_article(page, url)
            if data:
                articles.append(data)
            await asyncio.sleep(DELAY)

        # Write JSON
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        print(f"Wrote {len(articles)} articles to {OUTPUT_FILE}")

        # Do not close the attached Chrome; just close pages/contexts we opened
        try:
            await context.close()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
