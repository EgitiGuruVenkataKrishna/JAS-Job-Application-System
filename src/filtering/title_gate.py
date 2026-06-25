"""Layer 1 Title Gate — The Bouncer.

Filters out irrelevant or senior roles before running any description scraping
or LLM operations to save network requests and API tokens.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Drop if any of these are present
_NEGATIVE_KEYWORDS = (
    "senior",
    "lead",
    "manager",
    "nurse",
    "civil",
    "sales",
    "marketing",
)

# Must contain at least one from this group
_INTERN_KEYWORDS = (
    "intern",
    "internship",
    "co-op",
    "trainee",
)

# Must contain at least one from this group
_TECH_KEYWORDS = (
    "software",
    "ai",
    "data",
    "backend",
    "frontend",
    "fullstack",
    "developer",
    "engineer",
    "machine learning",
    "python",
    "web",
    "cloud",
    "devops",
    "cybersecurity",
    "security",
)


def passes_title_gate(title: str) -> bool:
    """Check if a job title passes the Layer 1 keyword filter.

    To pass:
      1. Must NOT contain any negative keywords (case-insensitive).
      2. Must contain at least one intern/co-op keyword (case-insensitive).
      3. Must contain at least one tech-related keyword (case-insensitive).

    Parameters
    ----------
    title : str
        The raw job title to check.

    Returns
    -------
    bool
        True if the title passes, False if it is rejected.
    """
    if not title:
        return False

    title_lower = title.lower()

    # 1. Negative Filter
    for neg_kw in _NEGATIVE_KEYWORDS:
        if neg_kw in title_lower:
            logger.info("Title Gate REJECTED negative keyword '%s': %s", neg_kw, title)
            return False

    # 2. Positive Filters (Intern variation AND Tech variation)
    has_intern = any(intern_kw in title_lower for intern_kw in _INTERN_KEYWORDS)
    has_tech = any(tech_kw in title_lower for tech_kw in _TECH_KEYWORDS)

    if not has_intern:
        logger.info("Title Gate REJECTED missing intern keyword: %s", title)
        return False

    if not has_tech:
        logger.info("Title Gate REJECTED missing tech keyword: %s", title)
        return False

    logger.info("Title Gate PASSED: %s", title)
    return True
