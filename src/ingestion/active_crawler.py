"""Active Discovery Engine — Scrapes trusted job platforms.

Fetches job listings directly from YC Startup Jobs (via Algolia API),
Internshala (via public HTML parser), and Hacker News / public RSS boards
as a fallback for Glassdoor/Wellfound.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
import httpx
from bs4 import BeautifulSoup

from src.ingestion.gmail_reader import RawJob
from src.filtering.title_gate import passes_title_gate

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class ActiveDiscoveryEngine:
    """Hourly background crawler for trusted platforms."""

    async def discover_jobs(self) -> list[RawJob]:
        """Query all trusted platforms and return matching RawJob instances.

        Runs titles through the Layer 1 Title Gate bouncer instantly.
        """
        all_jobs: list[RawJob] = []

        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            # 1. Fetch YC startup jobs
            yc_jobs = await self._fetch_yc_jobs(client)
            all_jobs.extend(yc_jobs)

            # 2. Fetch Internshala jobs
            internshala_jobs = await self._fetch_internshala_jobs(client)
            all_jobs.extend(internshala_jobs)

            # 3. Fetch public RSS jobs (Glassdoor/Wellfound fallback)
            rss_jobs = await self._fetch_rss_jobs(client)
            all_jobs.extend(rss_jobs)

        logger.info(
            "Active Discovery completed: staged %d raw jobs passing Layer 1 bouncer",
            len(all_jobs),
        )
        return all_jobs

    async def _fetch_yc_jobs(self, client: httpx.AsyncClient) -> list[RawJob]:
        """Query WorkAtAStartup index using public search key."""
        jobs: list[RawJob] = []
        try:
            url = "https://h9wd4gg0df-dsn.algolia.net/1/indexes/StartupJob_production/query"
            headers = {
                "x-algolia-application-id": "H9WD4GG0DF",
                "x-algolia-api-key": "180f339cf01ef5718af22687f975d045",
                "Content-Type": "application/json",
            }
            payload = {
                "params": "query=internship&hitsPerPage=30"
            }
            res = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if res.status_code == 200:
                hits = res.json().get("hits", [])
                for hit in hits:
                    title = hit.get("title", "")
                    company = hit.get("companyName", "")
                    job_id = hit.get("id")
                    job_url = f"https://www.workatastartup.com/jobs/{job_id}" if job_id else ""
                    location = hit.get("location", "Remote")
                    jd_text = hit.get("description", "") or hit.get("aboutRole", "")
                    
                    if job_url and passes_title_gate(title):
                        jobs.append(RawJob(
                            url=job_url,
                            platform="wellfound",  # Log as wellfound (trusted)
                            title=title,
                            company=company,
                            location=location,
                            jd_text=jd_text,
                        ))
            logger.info("YC fetch complete: found %d matching jobs", len(jobs))
        except Exception as e:
            logger.error("Failed to fetch YC jobs: %s", e)
        return jobs

    async def _fetch_internshala_jobs(self, client: httpx.AsyncClient) -> list[RawJob]:
        """Scrape Internshala's software developer internship listings."""
        jobs: list[RawJob] = []
        try:
            url = "https://internshala.com/internships/keywords-software-development"
            res = await client.get(url, timeout=15.0)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                containers = soup.select(".internship_meta")
                for container in containers:
                    title_elem = container.select_one(".job-title-container a")
                    company_elem = container.select_one(".company-name")
                    location_elem = container.select_one(".location_names")
                    
                    if title_elem and company_elem:
                        title = title_elem.get_text(strip=True)
                        company = company_elem.get_text(strip=True)
                        href = title_elem.get("href", "")
                        job_url = f"https://internshala.com{href}" if href.startswith("/") else href
                        location = location_elem.get_text(strip=True) if location_elem else "Remote"
                        
                        if job_url and passes_title_gate(title):
                            jobs.append(RawJob(
                                url=job_url,
                                platform="internshala",
                                title=title,
                                company=company,
                                location=location,
                                jd_text="",  # Will be fetched on-demand during pipeline
                            ))
            logger.info("Internshala fetch complete: found %d matching jobs", len(jobs))
        except Exception as e:
            logger.error("Failed to fetch Internshala jobs: %s", e)
        return jobs

    async def _fetch_rss_jobs(self, client: httpx.AsyncClient) -> list[RawJob]:
        """Fetch Hacker News jobs RSS feed as fallback for Glassdoor."""
        jobs: list[RawJob] = []
        try:
            url = "https://hnrss.org/jobs"
            res = await client.get(url, timeout=10.0)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title_full = item.find("title").text if item.find("title") else ""
                    link = item.find("link").text if item.find("link") else ""
                    description = item.find("description").text if item.find("description") else ""
                    
                    if "hiring" in title_full.lower():
                        parts = title_full.split("hiring")
                        company = parts[0].strip()
                        title = parts[1].strip().strip("a ").strip()
                    else:
                        company = "HN Startup"
                        title = title_full
                        
                    if link and passes_title_gate(title):
                        jobs.append(RawJob(
                            url=link,
                            platform="glassdoor",
                            title=title,
                            company=company,
                            location="Remote",
                            jd_text=description,
                        ))
            logger.info("RSS fetch complete: found %d matching jobs", len(jobs))
        except Exception as e:
            logger.error("Failed to fetch RSS fallback jobs: %s", e)
        return jobs
