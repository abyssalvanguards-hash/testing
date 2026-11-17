"""Editable JSON knowledge base used to ground ticket AI replies."""
import json
import math
import re
from pathlib import Path


TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
SECTIONS = ("faq", "rules", "products", "services", "links", "custom_responses")


def _tokens(value):
    return set(TOKEN_RE.findall(str(value or "").lower()))


class KnowledgeBase:
    """Read-only retrieval from an owner-editable JSON knowledge file."""

    def __init__(self, path=None):
        self.path = Path(path) if path else (
            Path(__file__).resolve().parents[1] / "data" / "knowledge_base.json"
        )

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def search(self, query, limit=4):
        query_terms = _tokens(query)
        if not query_terms:
            return []

        data = self._load()
        matches = []
        for section in SECTIONS:
            records = data.get(section, [])
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                searchable = " ".join(
                    str(record.get(key, ""))
                    for key in ("question", "title", "name", "keywords", "tags", "description")
                )
                record_terms = _tokens(searchable)
                overlap = query_terms.intersection(record_terms)
                if not overlap:
                    continue
                score = len(overlap) / math.sqrt(
                    max(1, len(query_terms)) * max(1, len(record_terms))
                )
                matches.append({
                    "section": section,
                    "title": (
                        record.get("question")
                        or record.get("title")
                        or record.get("name")
                        or section.replace("_", " ").title()
                    ),
                    "answer": (
                        record.get("answer")
                        or record.get("content")
                        or record.get("description")
                        or ""
                    ),
                    "url": record.get("url", ""),
                    "score": score,
                })

        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:max(1, int(limit))]

    def format_context(self, matches):
        if not matches:
            return "No relevant entry was found in the configured server knowledge base."

        blocks = []
        for index, item in enumerate(matches, start=1):
            block = (
                f"[{index}] Category: {item['section']}\n"
                f"Title: {item['title']}\n"
                f"Information: {item['answer']}"
            )
            if item.get("url"):
                block += f"\nURL: {item['url']}"
            blocks.append(block)
        return "\n\n".join(blocks)
