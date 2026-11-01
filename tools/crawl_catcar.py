#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Toyota/Lexus Japan EPC crawler for PartScope.

The crawler follows Catcar's real link-driven navigation tree. It writes compact
SKU shards incrementally, so an interrupted GitHub Actions run can be resumed.
"""
import argparse
import json
import logging
import os
import random
import re
import sys
import time
from collections import deque
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.catcar.info"
START_URL = (
    "https://www.catcar.info/toyota/"
    "?l=bWFya2V0PT1qYXBhbnx8c3Q9PTIwfHxzdHM9PXsiMTAiOiJSZWdpb24iLCIyMCI6IkphcGFuIn0%3D&lang=ru"
)
OUTPUT_DIR = os.path.join("public", "data")
STATE_FILE = "progress.json"
REQUEST_TIMEOUT = 35
MAX_ATTEMPTS = 3
MIN_DELAY = 1.5
MAX_DELAY = 3.5
MAX_PAGES_DEFAULT = 0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

# Toyota OEM numbers are normally 10 digits, optionally with a suffix.
OEM_RE = re.compile(r"(?<![A-Z0-9])[0-9A-Z]{5,}(?:[\s.-]+[0-9A-Z]{1,})?(?![A-Z0-9])", re.I)
INTERNAL_PATHS = ("/toyota/", "/lexus/")


def clean_oem(value):
    """Normalize an OEM exactly as required by PartScope SKU sharding."""
    return re.sub(r"[\s\-.]", "", str(value or "")).upper()


def shard_for(oem):
    cleaned = clean_oem(oem)
    return (cleaned[:2] if len(cleaned) >= 2 else "XX") + ".json"


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


class ShardWriter:
    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self.cache = {}
        self.dirty = set()
        self.oem_count = 0
        self._load_existing()

    def _load_existing(self):
        for filename in os.listdir(self.directory):
            if not filename.lower().endswith(".json"):
                continue
            path = os.path.join(self.directory, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    self.cache[filename] = data
            except (OSError, ValueError):
                logging.warning("Skipping unreadable shard: %s", path)
        self.oem_count = sum(len(data) for data in self.cache.values())

    def add(self, raw_oem, name, scheme):
        cleaned = clean_oem(raw_oem)
        if not cleaned:
            return False
        filename = shard_for(cleaned)
        shard = self.cache.setdefault(filename, {})
        record = shard.setdefault(cleaned, {
            "oem": str(raw_oem).strip(),
            "name": str(name or "").strip(),
            "schemes": [],
        })
        if not record.get("name") and name:
            record["name"] = str(name).strip()
        if not record.get("oem"):
            record["oem"] = str(raw_oem).strip()
        schemes = record.setdefault("schemes", [])
        normalized_scheme = {
            "img": str(scheme.get("img") or ""),
            "pos": str(scheme.get("pos") or ""),
            "car": str(scheme.get("car") or ""),
        }
        if normalized_scheme not in schemes:
            schemes.append(normalized_scheme)
            self.dirty.add(filename)
        return True

    def flush(self):
        for filename in sorted(self.dirty):
            path = os.path.join(self.directory, filename)
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8", newline="") as handle:
                json.dump(self.cache[filename], handle, ensure_ascii=False,
                          separators=(",", ":"))
            os.replace(temporary, path)
        self.dirty.clear()
        self.oem_count = sum(len(data) for data in self.cache.values())


class Progress:
    def __init__(self, path):
        self.path = path
        self.data = load_json(path, {
            "version": 1,
            "completed_subgroups": [],
            "visited_pages": [],
            "failed_urls": [],
        })
        for key in ("completed_subgroups", "visited_pages", "failed_urls"):
            self.data.setdefault(key, [])

    def save(self):
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, self.path)

    def contains(self, key, url):
        return url in self.data[key]

    def add(self, key, url):
        if url not in self.data[key]:
            self.data[key].append(url)
            self.save()


class CatcarCrawler:
    def __init__(self, writer, progress, max_pages=0, delay_min=MIN_DELAY,
                 delay_max=MAX_DELAY, start_url=START_URL):
        self.writer = writer
        self.progress = progress
        self.max_pages = max_pages
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.start_url = start_url
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        })
        self.pages_this_run = 0

    def get(self, url):
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.session.headers["User-Agent"] = random.choice(USER_AGENTS)
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if response.status_code in (429, 503):
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else (attempt * 8 + random.uniform(2, 5))
                    logging.warning("HTTP %s; retry %s/%s in %.1fs: %s",
                                    response.status_code, attempt, MAX_ATTEMPTS, wait, url)
                    if attempt < MAX_ATTEMPTS:
                        time.sleep(wait)
                        continue
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
                time.sleep(random.uniform(self.delay_min, self.delay_max))
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                wait = attempt * 3 + random.uniform(1, 3)
                logging.warning("Request failed (%s/%s): %s (%s)",
                                attempt, MAX_ATTEMPTS, url, exc)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(wait)
        raise RuntimeError("Unable to fetch {}: {}".format(url, last_error))

    @staticmethod
    def canonical_url(href, current_url):
        absolute = urljoin(current_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in (
                "www.catcar.info", "catcar.info"):
            return None
        if not any(parsed.path.startswith(path) for path in INTERNAL_PATHS):
            return None
        if "l" not in parse_qs(parsed.query):
            return None
        return absolute

    @staticmethod
    def page_title(soup):
        heading = soup.find(["h1", "h2"])
        return heading.get_text(" ", strip=True) if heading else ""

    @staticmethod
    def page_car_context(soup, url):
        # Breadcrumb labels and document title carry model/series information.
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        heading = CatcarCrawler.page_title(soup)
        crumbs = []
        for node in soup.select("nav a, .breadcrumb a, [class*=breadcrumb] a"):
            value = node.get_text(" ", strip=True)
            if value and value not in crumbs:
                crumbs.append(value)
        context = " / ".join(crumbs)
        if not context:
            context = re.sub(r"\s*-\s*Catcar\.info.*$", "", title).strip()
        if heading and heading.lower() not in context.lower():
            context = (context + " / " + heading).strip(" /")
        return context or title or url

    @staticmethod
    def is_parts_page(soup):
        text = soup.get_text(" ", strip=True).lower()
        has_part_table = False
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) >= 2:
                has_part_table = True
                break
        return has_part_table and (
            "subgroup" in text or "список деталей" in text or
            "наименование" in text or "part number" in text or
            "name" in text
        )

    @staticmethod
    def extract_scheme_images(soup, page_url):
        found = []
        for img in soup.find_all("img"):
            candidates = [
                img.get("src"), img.get("data-src"), img.get("data-original"),
                img.get("data-lazy-src"),
            ]
            parent = img.find_parent("a")
            if parent and parent.get("href"):
                candidates.append(parent.get("href"))
            for candidate in candidates:
                if not candidate:
                    continue
                absolute = urljoin(page_url, candidate.strip())
                parsed = urlparse(absolute)
                if parsed.scheme not in ("http", "https"):
                    continue
                path = parsed.path.lower()
                if any(token in path for token in ("logo", "banner", "flag", "icon", "captcha")):
                    continue
                if absolute not in found:
                    found.append(absolute)
        # Prefer Catcar's diagram CDN over site chrome or tracking images.
        found.sort(key=lambda value: (
            0 if "ci.catcar.info" in urlparse(value).netloc else 1,
            0 if any(x in urlparse(value).path.lower() for x in ("img", "image", "scheme", "catalog")) else 1,
        ))
        return found

    @staticmethod
    def looks_like_oem(value):
        candidate = str(value or "").strip()
        if not candidate or len(clean_oem(candidate)) < 5:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\s.\-/]{3,}", candidate))

    def extract_parts(self, soup, page_url, car_context):
        images = self.extract_scheme_images(soup, page_url)
        scheme_image = images[0] if images else ""
        extracted = 0

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_cells = rows[0].find_all(["th", "td"])
            headers = [cell.get_text(" ", strip=True).lower() for cell in header_cells]
            header_text = " ".join(headers)
            # Ignore layout tables unless they contain a recognizable parts table.
            if headers and not any(word in header_text for word in (
                    "name", "наимен", "part", "номер", "code", "код", "position", "поз")):
                continue

            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                values = [cell.get_text(" ", strip=True) for cell in cells]
                values = [value for value in values if value]
                if not values:
                    continue

                raw_oem = ""
                oem_index = -1
                for index, value in enumerate(values):
                    # Avoid treating numeric position codes as OEM numbers.
                    if self.looks_like_oem(value) and re.search(r"\d", value):
                        compact = clean_oem(value)
                        if len(compact) >= 8 or re.search(r"[-. ]", value):
                            raw_oem, oem_index = value, index
                            break
                if not raw_oem:
                    # Some catalog layouts place the OEM inside a link or bold node.
                    for node in row.find_all(["a", "b", "strong", "span"]):
                        value = node.get_text(" ", strip=True)
                        if self.looks_like_oem(value) and len(clean_oem(value)) >= 8:
                            raw_oem = value
                            break
                if not raw_oem:
                    continue

                pos = values[0] if values else ""
                if pos == raw_oem:
                    pos = ""
                name_candidates = [
                    value for index, value in enumerate(values)
                    if index != oem_index and value != pos and not self.looks_like_oem(value)
                ]
                name = name_candidates[-1] if name_candidates else ""
                if not name:
                    name = row.get_text(" ", strip=True).replace(raw_oem, "").strip(" |\t")
                scheme = {"img": scheme_image, "pos": pos, "car": car_context}
                if self.writer.add(raw_oem, name, scheme):
                    extracted += 1

        self.writer.flush()
        return extracted, scheme_image

    def child_links(self, soup, page_url):
        links = []
        seen = set()
        for anchor in soup.find_all("a", href=True):
            target = self.canonical_url(anchor["href"], page_url)
            if target and target not in seen:
                seen.add(target)
                links.append((target, anchor.get_text(" ", strip=True)))
        return links

    def crawl(self):
        queue = deque([(self.start_url, "Japan")])
        queued = {self.start_url}
        visited = set(self.progress.data["visited_pages"])
        completed = set(self.progress.data["completed_subgroups"])

        while queue:
            url, label = queue.popleft()
            if url in visited:
                continue
            if self.max_pages and self.pages_this_run >= self.max_pages:
                logging.info("Reached --max-pages=%s; stopping cleanly.", self.max_pages)
                break

            try:
                html = self.get(url)
            except Exception as exc:
                logging.error("Page failed and will remain resumable: %s (%s)", url, exc)
                if url not in self.progress.data["failed_urls"]:
                    self.progress.data["failed_urls"].append(url)
                    self.progress.save()
                continue

            soup = BeautifulSoup(html, "html.parser")
            title = self.page_title(soup)
            car_context = self.page_car_context(soup, url)
            self.pages_this_run += 1
            visited.add(url)
            self.progress.add("visited_pages", url)

            logging.info("PAGE %s | %s | %s | compiled OEMs: %s",
                         self.pages_this_run, label or title or "catalog",
                         title or url, self.writer.oem_count)

            if self.is_parts_page(soup):
                if url not in completed:
                    count, image_url = self.extract_parts(soup, url, car_context)
                    completed.add(url)
                    self.progress.add("completed_subgroups", url)
                    logging.info(
                        "SUBGROUP DONE | %s | new part rows: %s | scheme: %s | total OEMs: %s",
                        title or label, count, image_url or "not found", self.writer.oem_count
                    )
                continue

            children = self.child_links(soup, url)
            if not children:
                logging.warning("No catalog child links found: %s", url)
            for child_url, child_label in children:
                if child_url not in queued and child_url not in visited:
                    queued.add(child_url)
                    queue.append((child_url, child_label or label))

        self.writer.flush()
        self.progress.save()
        logging.info(
            "CRAWL FINISHED | pages this run: %s | completed subgroups: %s | total OEMs: %s",
            self.pages_this_run, len(self.progress.data["completed_subgroups"]),
            self.writer.oem_count
        )


def main():
    parser = argparse.ArgumentParser(description="Crawl Toyota/Lexus Japan EPC into PartScope SKU shards.")
    parser.add_argument("--start-url", default=START_URL, help="Catalog market URL.")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument("--progress", default=STATE_FILE)
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT,
                        help="Maximum pages in this run; 0 means unlimited.")
    parser.add_argument("--delay-min", type=float, default=MIN_DELAY)
    parser.add_argument("--delay-max", type=float, default=MAX_DELAY)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        parser.error("Invalid delay range.")

    os.makedirs(args.output_dir, exist_ok=True)
    writer = ShardWriter(args.output_dir)
    progress = Progress(args.progress)
    logging.info("Starting Toyota/Lexus Japan crawl; existing OEMs: %s", writer.oem_count)
    crawler = CatcarCrawler(
        writer, progress, max_pages=args.max_pages,
        delay_min=args.delay_min, delay_max=args.delay_max,
        start_url=args.start_url,
    )
    try:
        crawler.crawl()
    except KeyboardInterrupt:
        writer.flush()
        progress.save()
        logging.warning("Interrupted; progress and shards have been saved.")
        raise


if __name__ == "__main__":
    main()
