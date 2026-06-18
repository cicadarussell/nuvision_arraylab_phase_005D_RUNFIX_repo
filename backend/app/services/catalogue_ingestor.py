from __future__ import annotations
from dataclasses import dataclass, asdict
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

@dataclass
class IngestedProduct:
    source_url: str
    title: str
    handle: str | None = None
    vendor: str | None = None
    category_hint: str | None = None
    datasheet_links: list[str] | None = None

DATASHEET_KEYWORDS = ["datasheet", "data sheet", "technical data", "manual", "mcs", "certificate", "installation guide", "user guide"]

async def fetch_html(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url, timeout=30)
    response.raise_for_status()
    return response.text

def extract_candidate_doc_links(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a"):
        text = " ".join(a.get_text(" ").lower().split())
        href = a.get("href")
        if not href:
            continue
        href_l = href.lower()
        if any(k in text or k in href_l for k in DATASHEET_KEYWORDS):
            links.append(urljoin(base_url, href))
    return sorted(set(links))

async def ingest_product_page(url: str) -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        html = await fetch_html(client, url)
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else "unknown"
    product = IngestedProduct(source_url=url, title=title, datasheet_links=extract_candidate_doc_links(url, html))
    return asdict(product)
