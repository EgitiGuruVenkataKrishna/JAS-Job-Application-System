"""Fallback handler — provides manual-apply information for unsupported ATS types."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class FallbackHandler:
    """Handles job applications that cannot be auto-filled.

    This handler does **not** attempt to fill any form.  Instead it returns
    structured data (direct URL + human-readable message) so the user can
    complete the application manually — typically via a Telegram notification.
    """

    async def handle(self, job_data: dict) -> dict:
        """Produce fallback information for a job that can't be auto-applied.

        Args:
            job_data: Must contain at least ``apply_url`` and ``title``.
                      Optionally includes ``company``, ``ats_type``, etc.

        Returns:
            A dict with keys:
                - ``manual_url``: direct application link.
                - ``message``: Telegram-friendly markdown message.
                - ``reason``: why auto-apply was skipped.
        """
        apply_url: str = job_data.get("apply_url", job_data.get("url", ""))
        title: str = job_data.get("title", "Unknown Position")
        company: str = job_data.get("company", "Unknown Company")
        ats_type: str = job_data.get("ats_type", "unknown")

        reason = f"Unsupported ATS type: {ats_type}"
        message = (
            f"⚠️ *Manual Application Required*\n\n"
            f"**{title}** at **{company}**\n"
            f"ATS: `{ats_type}` (not supported for auto-apply)\n\n"
            f"🔗 [Apply here]({apply_url})"
        )

        logger.info(
            "Fallback triggered for '%s' at '%s' — ATS: %s",
            title,
            company,
            ats_type,
        )

        return {
            "manual_url": apply_url,
            "message": message,
            "reason": reason,
        }
