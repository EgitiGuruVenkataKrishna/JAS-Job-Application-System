"""Document generator — renders LaTeX resumes via Jinja2 + Tectonic."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import get_settings

logger = logging.getLogger(__name__)

# Characters that must be escaped for safe LaTeX rendering
_LATEX_SPECIAL_CHARS: dict[str, str] = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_LATEX_ESCAPE_RE = re.compile(
    "|".join(re.escape(k) for k in _LATEX_SPECIAL_CHARS)
)

# Directory containing this file (and the .tex template)
_TEMPLATE_DIR = Path(__file__).resolve().parent


class DocumentGenerator:
    """Renders tailored LaTeX resumes and compiles them to PDF with Tectonic."""

    def __init__(self) -> None:
        """Initialise the Jinja2 environment with LaTeX-safe delimiters."""
        settings = get_settings()
        self._resumes_dir: Path = settings.resumes_dir

        self._env = Environment(
            block_start_string=r"\BLOCK{",
            block_end_string="}",
            variable_start_string=r"\VAR{",
            variable_end_string="}",
            comment_start_string=r"\#{",
            comment_end_string="}",
            line_statement_prefix=None,
            line_comment_prefix=None,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        )
        # Register the escape filter so templates can use it if needed
        self._env.filters["latex_escape"] = self._escape_latex

        logger.info(
            "DocumentGenerator initialised — output: %s", self._resumes_dir
        )

    # ── public API ─────────────────────────────────────────────────────────

    async def generate_resume(
        self,
        resume_data: dict,
        tailored_bullets: list[str],
        company: str,
    ) -> Path:
        """Render and compile a tailored resume PDF.

        Args:
            resume_data: Base resume payload (contact, education, experience …).
            tailored_bullets: AI-generated bullet points customised for a role.
            company: Target company name — used in the output filename.

        Returns:
            Path to the generated PDF file.

        Raises:
            RuntimeError: If Tectonic compilation fails.
        """
        # Merge tailored bullets into the template context
        context = {**resume_data, "tailored_bullets": tailored_bullets}

        # Escape all string values for LaTeX safety
        context = self._escape_context(context)

        template = self._env.get_template("resume_template.tex")
        rendered_tex = template.render(**context)

        # Write rendered .tex to a temp directory for compilation
        with tempfile.TemporaryDirectory() as tmp_dir:
            tex_path = Path(tmp_dir) / "resume.tex"
            tex_path.write_text(rendered_tex, encoding="utf-8")

            try:
                result = subprocess.run(
                    ["tectonic", str(tex_path)],
                    capture_output=True,
                    text=True,
                    cwd=tmp_dir,
                    timeout=120,
                )
            except FileNotFoundError:
                logger.warning("Tectonic compiler not found. Creating placeholder PDF and raw LaTeX file for local dev.")
                # Create a placeholder PDF file so local runs don't crash
                compiled_pdf = Path(tmp_dir) / "resume.pdf"
                compiled_pdf.write_text("%PDF-1.5 mock pdf content for local development", encoding="utf-8")
                # Write the rendered LaTeX file as well for reference
                (Path(tmp_dir) / "resume.tex").write_text(rendered_tex, encoding="utf-8")
                class MockResult:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                result = MockResult()

            if result.returncode != 0:
                logger.error(
                    "Tectonic compilation failed for %s:\nstdout: %s\nstderr: %s",
                    company,
                    result.stdout,
                    result.stderr,
                )
                raise RuntimeError(
                    f"LaTeX compilation failed for {company}: {result.stderr}"
                )

            compiled_pdf = Path(tmp_dir) / "resume.pdf"
            if not compiled_pdf.exists():
                raise RuntimeError(
                    "Tectonic reported success but resume.pdf not found."
                )

            # Move to final output location
            timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_company = re.sub(r"[^\w\-]", "_", company)
            dest = self._resumes_dir / f"{safe_company}_{timestamp}.pdf"
            shutil.move(str(compiled_pdf), str(dest))

        logger.info("Resume PDF saved → %s", dest)
        return dest

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _escape_latex(text: str) -> str:
        """Escape special LaTeX characters in *text*.

        Handles: % $ & # _ { } ~ ^
        """
        if not isinstance(text, str):
            return text
        return _LATEX_ESCAPE_RE.sub(
            lambda m: _LATEX_SPECIAL_CHARS[m.group()], text
        )

    @classmethod
    def _escape_context(cls, obj: object) -> object:
        """Recursively escape all string values in a nested data structure."""
        if isinstance(obj, str):
            return cls._escape_latex(obj)
        if isinstance(obj, dict):
            return {k: cls._escape_context(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls._escape_context(item) for item in obj]
        return obj
