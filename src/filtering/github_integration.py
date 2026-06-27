"""GitHub Integration module.

Queries the GitHub API for the user's repositories and structures them for
injection into the AI matching/tailoring prompt.
"""

from __future__ import annotations

import logging
import httpx
from typing import Any

logger = logging.getLogger(__name__)

# Predefined metadata for the user's major high-value repositories.
# This ensures that even if GitHub API descriptions are brief, the LLM has
# rich context about the projects (tech stack, features, and capabilities).
_ENRICHED_PROJECTS = {
    "JAS-Job-Application-System": {
        "name": "JAS — Job Application System",
        "tech_stack": "Python · FastAPI · Supabase · pgvector · Gemini API · Playwright · Docker · GitHub Actions",
        "description": "An autonomous AI job search and application system.",
        "bullets": [
            "Engineered a multi-stage funnel including a zero-cost Title Gate, vector-based Math Gate, and LLM evaluator via Gemini 2.5 Flash.",
            "Designed a PostgreSQL + pgvector database layer to perform 90%+ cosine similarity deduplication on the fly in Python.",
            "Built a stealth Playwright auto-applier utilizing playwright-stealth to bypass Cloudflare/Datadome bot securities."
        ]
    },
    "Sume-AI": {
        "name": "Sume AI — ATS Resume Optimiser",
        "tech_stack": "Python · FastAPI · Groq · Redis · PyPDF2 · Pydantic · Docker · Vercel",
        "description": "An ATS resume analysis and optimization SaaS.",
        "bullets": [
            "Built an ATS resume evaluator using Groq LLMs and PyPDF2, providing real-time scoring, gap analysis, and tailored bullet recommendations.",
            "Designed a 3-tier API rate-limiter with Redis and FastAPI sustaining 1,000+ requests per minute under <1 ms overhead.",
            "Architected Pydantic-validated structured outputs for stateless and deterministic resume grading."
        ]
    },
    "smart-library-system": {
        "name": "Smart Library Management System",
        "tech_stack": "Python · FastAPI · PostgreSQL · Docker · HTML/JS",
        "description": "A full-stack library platform serving 500+ active users.",
        "bullets": [
            "Developed a full-stack library portal integrated with a Fuzzy Logic search engine for typo-resilient book discovery.",
            "Reduced deployment overhead by 95% through complete containerization of the system via Docker."
        ]
    },
    "ProductionLevel_RAG": {
        "name": "ProductionLevel RAG — Legal Q&A System",
        "tech_stack": "Python · FastAPI · FAISS · BM25S · Groq (Llama-3.1-8b) · sentence-transformers · Docker",
        "description": "An 8-stage production RAG pipeline covering multi-statute legal codes.",
        "bullets": [
            "Engineered an 8-stage legal RAG pipeline with multi-query expansion, hybrid search (BM25S + FAISS), and Reciprocal Rank Fusion.",
            "Integrated cross-encoder reranking and context filtering to maximize retrieval precision over large statute databases.",
            "Enforced strict safety refusals for low-confidence lookups, preventing hallucinations of legal provisions."
        ]
    },
    "smart-parking-system": {
        "name": "Smart Parking Reservation System",
        "tech_stack": "Python · Flask · SQLite · IoT Simulation · WebSockets",
        "description": "An IoT-based real-time parking spot reservation and tracking system.",
        "bullets": [
            "Built a real-time parking spot allocation system with Flask and WebSockets, simulating sensor inputs for slot availability.",
            "Designed an interactive dashboard displaying parking occupancy metrics and automated reservation lifecycles."
        ]
    },
    "smart-resource-allocator": {
        "name": "AI Smart Resource Allocator",
        "tech_stack": "Python · NumPy · Scikit-Learn · FastAPI",
        "description": "An intelligent compute-resource allocator for cluster nodes.",
        "bullets": [
            "Developed an AI model to dynamically allocate CPU and RAM on cluster nodes based on workload prediction.",
            "Achieved a 30% reduction in compute waste by optimizing task scheduling in a simulated environment."
        ]
    },
    "Teleprompter": {
        "name": "Web Teleprompter & Speech Tracker",
        "tech_stack": "JavaScript · HTML5 · Web Speech API · CSS3",
        "description": "A real-time voice-activated scrolling teleprompter.",
        "bullets": [
            "Created an in-browser voice-scrolling teleprompter using the Web Speech API for real-time speech recognition and pacing.",
            "Implemented custom adjustment controls for scroll speed, text styling, and prompt overlays."
        ]
    }
}

class GitHubIntegration:
    """Fetches user GitHub repositories and formats them for the LLM."""

    def __init__(self, username: str = "EgitiGuruVenkataKrishna") -> None:
        self.username = username
        self.api_url = f"https://api.github.com/users/{username}/repos"

    async def get_projects_for_prompt(self) -> list[dict[str, Any]]:
        """Fetch repos from GitHub API and enrich them with metadata."""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    self.api_url,
                    headers={"User-Agent": "JAS-Agent/2.0"}
                )
                if response.status_code == 200:
                    repos = response.json()
                    repo_names = [r.get("name") for r in repos if r.get("name")]
                    logger.info("Fetched %d repositories from GitHub for %s", len(repo_names), self.username)
                    return self._build_project_list(repo_names)
                else:
                    logger.warning("GitHub API returned status %d. Using local cache.", response.status_code)
        except Exception as e:
            logger.error("Failed to fetch GitHub repos: %s. Using local cache.", e)

        # Fallback to local keys if API is rate-limited or offline
        return self._build_project_list(list(_ENRICHED_PROJECTS.keys()))

    def _build_project_list(self, repo_names: list[str]) -> list[dict[str, Any]]:
        """Merge fetched repo names with detailed descriptions and bullet points."""
        projects = []
        for name in repo_names:
            if name in _ENRICHED_PROJECTS:
                projects.append(_ENRICHED_PROJECTS[name])
            else:
                # Add default details for other repos
                projects.append({
                    "name": name.replace("-", " ").title(),
                    "tech_stack": "Python · Git",
                    "description": f"Public repository: {name} on GitHub.",
                    "bullets": [
                        f"Developed and maintained the {name} project on GitHub, applying best practices for version control.",
                        "Implemented core algorithms and modular structure for codebase scalability."
                    ]
                })
        return projects
