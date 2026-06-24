"""JAS Playwright Auto-Applier Engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

from src.applier.ats_detector import AtsDetector, AtsType
from src.applier.fallback import FallbackHandler
from src.applier.modules.greenhouse import GreenhouseModule
from src.applier.modules.lever import LeverModule
from src.applier.modules.ashby import AshbyModule
from src.applier.modules.generic import GenericModule
from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    success: bool
    method: str
    screenshot_path: str | None
    error: str | None


class AutoApplier:
    """Core orchestrator for auto-applying to jobs via Playwright."""

    def __init__(self):
        self.settings = get_settings()
        self.ats_detector = AtsDetector()
        self.fallback = FallbackHandler()
        
        self.modules = {
            AtsType.GREENHOUSE: GreenhouseModule(),
            AtsType.LEVER: LeverModule(),
            AtsType.ASHBY: AshbyModule(),
            AtsType.GENERIC: GenericModule(),
        }

    async def apply(self, job_data: dict[str, Any], resume_path: str, cover_letter_path: str | None = None) -> ApplyResult:
        """Launch Playwright, detect ATS, and apply."""
        url = job_data.get("url", "")
        company = job_data.get("company", "unknown_company")
        
        logger.info(f"Starting auto-apply for {url}")
        
        async with async_playwright() as p:
            # Mask Playwright identity
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            # 🛡️ STEALTH: Mask all bot fingerprints
            await stealth_async(page)
            
            try:
                await page.goto(url, wait_until="networkidle")
                
                # Detect ATS
                ats_type = await self.ats_detector.detect(page)
                logger.info(f"Detected ATS Type: {ats_type.name}")
                
                if ats_type in [AtsType.WORKDAY, AtsType.UNKNOWN]:
                    logger.warning(f"Unsupported ATS ({ats_type.name}) - using fallback.")
                    # Return fallback message
                    fallback_result = await self.fallback.handle(job_data)
                    return ApplyResult(
                        success=False,
                        method="fallback",
                        screenshot_path=None,
                        error=f"Unsupported ATS: {ats_type.name}. Use manual link.",
                    )
                
                # Run ATS module
                module = self.modules.get(ats_type)
                if not module:
                    raise ValueError(f"No module mapped for ATS {ats_type.name}")
                
                success = await module.fill_and_submit(
                    page, 
                    user_profile=job_data.get("user_profile", {}), 
                    resume_path=resume_path, 
                    cover_letter_path=cover_letter_path
                )
                
                # Take screenshot
                screenshot_path = str(self.settings.screenshots_dir / f"{company}_apply_{ats_type.name.lower()}.png")
                await page.screenshot(path=screenshot_path, full_page=True)
                
                if success:
                    logger.info("Application submitted successfully.")
                    return ApplyResult(success=True, method=ats_type.name.lower(), screenshot_path=screenshot_path, error=None)
                else:
                    logger.error("Failed to submit application.")
                    return ApplyResult(success=False, method=ats_type.name.lower(), screenshot_path=screenshot_path, error="Form filling/submission failed")
                    
            except Exception as e:
                logger.error(f"Error during auto-apply: {e}", exc_info=True)
                
                # Try to take error screenshot
                screenshot_path = str(self.settings.screenshots_dir / f"{company}_error.png")
                try:
                    await page.screenshot(path=screenshot_path)
                except Exception:
                    screenshot_path = None
                    
                return ApplyResult(success=False, method="error", screenshot_path=screenshot_path, error=str(e))
                
            finally:
                await browser.close()


async def auto_apply(job_id: str) -> dict[str, Any]:
    """Helper function called by the Telegram bot to apply to a job by ID.

    1. Instantiates DatabaseClient and fetches job details.
    2. Instantiates AutoApplier.
    3. Runs apply.
    4. Returns dict with success/error/screenshot_path/method.
    """
    from src.db.client import DatabaseClient
    
    db = DatabaseClient()
    await db.initialize()
    
    job_data = await db.get_job(job_id)
    if not job_data:
        return {"success": False, "error": f"Job {job_id} not found in database"}
        
    profile = await db.get_user_profile()
    if not profile:
        return {"success": False, "error": "User profile not found. Send your resume first."}
        
    full_name = profile.get("full_name", "")
    first_name = ""
    last_name = ""
    if full_name:
        parts = full_name.split(None, 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
        
    job_data["user_profile"] = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "linkedin_url": profile.get("linkedin_url", ""),
        **(profile.get("resume_json") or {})
    }
    
    applier = AutoApplier()
    resume_path = job_data.get("tailored_resume_path")
    cover_letter_path = job_data.get("cover_letter_path")
    
    if not resume_path:
        return {"success": False, "error": "No tailored resume path found for this job"}
        
    try:
        result = await applier.apply(
            job_data=job_data,
            resume_path=resume_path,
            cover_letter_path=cover_letter_path
        )
        return {
            "success": result.success,
            "method": result.method,
            "screenshot_path": result.screenshot_path,
            "error": result.error
        }
    except Exception as e:
        logger.exception("auto_apply failed for job %s", job_id)
        return {"success": False, "error": str(e)}
