"""JAS DatabaseClient — Async-compatible Supabase wrapper.

Provides all CRUD operations the pipeline needs:
  • Job dedup, insert, status update, pending-jobs listing
  • User profile CRUD
  • Resume embedding hot-swap (clear → store)
  • Daily stats for the Telegram digest
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client

from src.config import get_settings

logger = logging.getLogger(__name__)


class DatabaseClient:
    """Thin wrapper around the Supabase Python client.

    Usage::

        db = DatabaseClient()
        await db.initialize()
        exists = await db.job_exists_by_hash(url_hash)
    """

    def __init__(self) -> None:
        self._client: Client | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the Supabase client from env settings."""
        try:
            settings = get_settings()
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_key,
            )
            logger.info("Supabase client initialised (%s)", settings.supabase_url)
        except Exception:
            logger.exception("Failed to initialise Supabase client")
            raise

    @property
    def client(self) -> Client:
        """Return the live Supabase client, raising if uninitialised."""
        if self._client is None:
            raise RuntimeError(
                "DatabaseClient not initialised — call await db.initialize() first"
            )
        return self._client

    # ------------------------------------------------------------------
    # Jobs — dedup & ingestion
    # ------------------------------------------------------------------

    async def job_exists_by_hash(self, url_hash: str) -> bool:
        """Return *True* if a job with the given URL hash already exists."""
        try:
            response = (
                self.client
                .table("jobs_found")
                .select("id")
                .eq("url_hash", url_hash)
                .limit(1)
                .execute()
            )
            return len(response.data) > 0
        except Exception:
            logger.exception("job_exists_by_hash failed for hash=%s", url_hash)
            raise

    async def insert_job(self, job_data: dict) -> str:
        """Insert a new job row and return its UUID.

        Parameters
        ----------
        job_data : dict
            Must include at minimum ``url_hash``.  All columns from
            ``jobs_found`` are accepted.

        Returns
        -------
        str
            The ``id`` (UUID) of the newly created row.
        """
        try:
            response = (
                self.client
                .table("jobs_found")
                .insert(job_data)
                .execute()
            )
            new_id: str = response.data[0]["id"]
            logger.info("Inserted job %s (hash=%s)", new_id, job_data.get("url_hash"))
            return new_id
        except Exception:
            logger.exception("insert_job failed: %s", job_data.get("url_hash"))
            raise

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        **extras: Any,
    ) -> None:
        """Update a job's workflow status and any additional fields.

        Parameters
        ----------
        job_id : str
            UUID of the job row.
        status : str
            New status value (must satisfy the CHECK constraint).
        **extras
            Arbitrary extra columns to update (e.g. ``match_score=0.85``).
        """
        try:
            payload: dict[str, Any] = {"status": status, **extras}
            (
                self.client
                .table("jobs_found")
                .update(payload)
                .eq("id", job_id)
                .execute()
            )
            logger.info("Job %s → status=%s", job_id, status)
        except Exception:
            logger.exception("update_job_status failed for job_id=%s", job_id)
            raise

    async def get_job(self, job_id: str) -> dict | None:
        """Fetch a single job by its ID.

        Parameters
        ----------
        job_id : str
            UUID of the job.

        Returns
        -------
        dict | None
            The job dictionary if found, else None.
        """
        try:
            response = (
                self.client
                .table("jobs_found")
                .select("*")
                .eq("id", job_id)
                .limit(1)
                .execute()
            )
            if response.data:
                return response.data[0]
            return None
        except Exception:
            logger.exception("get_job failed for job_id=%s", job_id)
            raise

    async def get_pending_jobs(self) -> list[dict]:
        """Return jobs waiting for user action (status = 'PENDING_USER')."""
        try:
            response = (
                self.client
                .table("jobs_found")
                .select("*")
                .eq("status", "PENDING_USER")
                .order("discovered_at", desc=True)
                .execute()
            )
            return response.data
        except Exception:
            logger.exception("get_pending_jobs failed")
            raise

    # ------------------------------------------------------------------
    # Daily digest & stats
    # ------------------------------------------------------------------

    async def get_daily_stats(self) -> dict:
        """Aggregate counts for the nightly Telegram digest.

        Returns a dict like::

            {
                "scanned": 12,
                "math_passed": 8,
                "ai_passed": 3,
                "applied_auto": 1,
                "applied_manual": 1,
                "skipped": 1,
            }
        """
        try:
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0,
            ).isoformat()

            # Total jobs scanned today
            scanned_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .gte("discovered_at", today_start)
                .execute()
            )
            scanned = scanned_res.count or 0

            # Failed math gate today
            failed_math_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .gte("discovered_at", today_start)
                .eq("status", "FILTERED_MATH")
                .execute()
            )
            failed_math = failed_math_res.count or 0

            # Failed AI gate today
            failed_llm_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .gte("discovered_at", today_start)
                .eq("status", "FILTERED_LLM")
                .execute()
            )
            failed_llm = failed_llm_res.count or 0

            # Applied auto today
            applied_auto_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .gte("discovered_at", today_start)
                .eq("status", "APPLIED_AUTO")
                .execute()
            )
            applied_auto = applied_auto_res.count or 0

            # Applied manual today
            applied_manual_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .gte("discovered_at", today_start)
                .eq("status", "APPLIED_MANUAL")
                .execute()
            )
            applied_manual = applied_manual_res.count or 0

            # Skipped today
            skipped_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .gte("discovered_at", today_start)
                .eq("status", "SKIPPED")
                .execute()
            )
            skipped = skipped_res.count or 0

            math_passed = scanned - failed_math
            ai_passed = math_passed - failed_llm

            return {
                "scanned": scanned,
                "math_passed": math_passed,
                "ai_passed": ai_passed,
                "applied_auto": applied_auto,
                "applied_manual": applied_manual,
                "skipped": skipped,
            }
        except Exception:
            logger.exception("get_daily_stats failed")
            raise

    async def get_pipeline_stats(self) -> dict:
        """Get aggregate statistics across all time for the /status command.

        Returns a dict like::

            {
                "scanned": 120,
                "applied_auto": 25,
                "applied_manual": 10,
                "pending": 5,
                "skipped": 15,
                "unique_domains_count": 8,
                "platform_breakdown": {"linkedin": 45, "gmail": 75},
                "top_domains": ["greenhouse.io", "lever.co"]
            }
        """
        try:
            # Scanned
            scanned_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .execute()
            )
            scanned = scanned_res.count or 0

            # Applied auto
            applied_auto_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .eq("status", "APPLIED_AUTO")
                .execute()
            )
            applied_auto = applied_auto_res.count or 0

            # Applied manual
            applied_manual_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .eq("status", "APPLIED_MANUAL")
                .execute()
            )
            applied_manual = applied_manual_res.count or 0

            # Pending (PENDING_USER)
            pending_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .eq("status", "PENDING_USER")
                .execute()
            )
            pending = pending_res.count or 0

            # Skipped
            skipped_res = (
                self.client
                .table("jobs_found")
                .select("id", count="exact")
                .eq("status", "SKIPPED")
                .execute()
            )
            skipped = skipped_res.count or 0

            # Platforms and URLs for website stats
            platforms_res = (
                self.client
                .table("jobs_found")
                .select("platform, url")
                .execute()
            )
            
            platforms_count = {}
            domains = set()
            from urllib.parse import urlparse
            for row in (platforms_res.data or []):
                plat = row.get("platform") or "unknown"
                platforms_count[plat] = platforms_count.get(plat, 0) + 1
                
                url = row.get("url")
                if url:
                    try:
                        netloc = urlparse(url).netloc
                        if netloc:
                            domain_clean = netloc.lower().replace("www.", "")
                            if domain_clean:
                                domains.add(domain_clean)
                    except Exception:
                        pass

            # Sort domains to show top ones
            domain_counts = {}
            for row in (platforms_res.data or []):
                url = row.get("url")
                if url:
                    try:
                        netloc = urlparse(url).netloc
                        if netloc:
                            domain_clean = netloc.lower().replace("www.", "")
                            if domain_clean:
                                domain_counts[domain_clean] = domain_counts.get(domain_clean, 0) + 1
                    except Exception:
                        pass
            
            sorted_domains = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)
            top_domains = [d[0] for d in sorted_domains[:5]]

            return {
                "scanned": scanned,
                "applied_auto": applied_auto,
                "applied_manual": applied_manual,
                "pending": pending,
                "skipped": skipped,
                "unique_domains_count": len(domains),
                "platform_breakdown": platforms_count,
                "top_domains": top_domains,
            }
        except Exception:
            logger.exception("get_pipeline_stats failed")
            raise

    # ------------------------------------------------------------------
    # User profile
    # ------------------------------------------------------------------

    async def get_user_profile(self) -> dict | None:
        """Fetch the singleton user profile, or *None* if none exists."""
        try:
            response = (
                self.client
                .table("user_profile")
                .select("*")
                .limit(1)
                .execute()
            )
            if response.data:
                return response.data[0]
            return None
        except Exception:
            logger.exception("get_user_profile failed")
            raise

    async def upsert_user_profile(self, profile: dict) -> None:
        """Create or update the singleton user profile.

        Parameters
        ----------
        profile : dict
            Any subset of ``user_profile`` columns.  If a row already
            exists the provided columns are merged; otherwise a new row
            is created.
        """
        try:
            existing = await self.get_user_profile()
            if existing:
                (
                    self.client
                    .table("user_profile")
                    .update(profile)
                    .eq("id", existing["id"])
                    .execute()
                )
            else:
                (
                    self.client
                    .table("user_profile")
                    .insert(profile)
                    .execute()
                )
            logger.info("Upserted user profile")
        except Exception:
            logger.exception("upsert_user_profile failed")
            raise

    # ------------------------------------------------------------------
    # Resume embedding hot-swap
    # ------------------------------------------------------------------

    async def update_resume_embedding(self, embedding: list[float]) -> None:
        """Store a new resume embedding on the user profile.

        Call :meth:`clear_resume_embedding` first when hot-swapping
        resumes to guarantee a clean slate.

        Parameters
        ----------
        embedding : list[float]
            768-dimensional vector from ``text-embedding-004``.
        """
        try:
            profile = await self.get_user_profile()
            if profile is None:
                logger.warning(
                    "No user profile exists yet — creating one with embedding"
                )
                await self.upsert_user_profile(
                    {"resume_embedding": embedding}
                )
            else:
                (
                    self.client
                    .table("user_profile")
                    .update({"resume_embedding": embedding})
                    .eq("id", profile["id"])
                    .execute()
                )
            logger.info("Resume embedding updated (dim=%d)", len(embedding))
        except Exception:
            logger.exception("update_resume_embedding failed")
            raise

    async def clear_resume_embedding(self) -> None:
        """Wipe the resume embedding (and optionally resume_text).

        This is step 1 of a hot-swap: clear old data before storing new.
        """
        try:
            profile = await self.get_user_profile()
            if profile is None:
                logger.info("No profile to clear — skipping")
                return

            (
                self.client
                .table("user_profile")
                .update({
                    "resume_embedding": None,
                    "resume_text": "",
                })
                .eq("id", profile["id"])
                .execute()
            )
            logger.info("Resume embedding cleared for hot-swap")
        except Exception:
            logger.exception("clear_resume_embedding failed")
            raise
