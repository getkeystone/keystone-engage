"""Corpus loader for Keystone Engage.

Reads markdown files from the corpus directory, splits by ## headers,
returns chunks with metadata for provenance tracking.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from keystone_engage.vectorstore import ChunkRecord

logger = logging.getLogger(__name__)


def _extract_title(content: str) -> str:
    """Extract the first # heading as the document title."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return "Untitled"


def _chunk_markdown(content: str, source_file: str) -> list[ChunkRecord]:
    """Split markdown by ## headers. Each section becomes a chunk."""
    doc_title = _extract_title(content)
    sections: list[tuple[str, str]] = []

    current_title = "Introduction"
    current_body: list[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            body = "\n".join(current_body).strip()
            if body and len(body) > 20:
                sections.append((current_title, body))
            current_title = line[3:].strip()
            current_body = []
        else:
            current_body.append(line)

    body = "\n".join(current_body).strip()
    if body:
        sections.append((current_title, body))

    chunks = []
    for i, (section_title, section_body) in enumerate(sections):
        chunk = ChunkRecord(
            chunk_id=f"{source_file}::{i:03d}::{_slugify(section_title)}",
            content=f"Document: {doc_title}\nSection: {section_title}\n\n{section_body}",
            source_document=source_file,
            section=section_title,
        )
        chunks.append(chunk)

    return chunks


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40]


def load_corpus(corpus_dir: str | Path) -> list[ChunkRecord]:
    """Load all markdown files from the corpus directory and chunk them."""
    corpus_path = Path(corpus_dir)
    if not corpus_path.exists():
        logger.warning("Corpus directory does not exist: %s", corpus_path)
        return []

    all_chunks: list[ChunkRecord] = []
    md_files = sorted(corpus_path.glob("*.md"))

    if not md_files:
        logger.warning("No .md files found in %s", corpus_path)
        return []

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        chunks = _chunk_markdown(content, md_file.name)
        all_chunks.extend(chunks)
        logger.info("Loaded %d chunks from %s", len(chunks), md_file.name)

    logger.info("Total corpus: %d chunks from %d files", len(all_chunks), len(md_files))
    return all_chunks
