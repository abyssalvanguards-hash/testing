import json

from core.knowledge import KnowledgeBase


def test_search_returns_relevant_faq_and_link(tmp_path):
    path = tmp_path / "knowledge_base.json"
    path.write_text(json.dumps({
        "faq": [{
            "question": "How do I claim a free slot?",
            "answer": "Open the slot panel and choose Free."
        }],
        "links": [{
            "name": "Slot guide",
            "description": "Instructions for slot setup",
            "url": "https://example.invalid/slot-guide"
        }]
    }), encoding="utf-8")

    base = KnowledgeBase(path)
    matches = base.search("How can I claim a free slot?", limit=3)

    assert matches
    assert matches[0]["section"] == "faq"
    assert "choose Free" in base.format_context(matches)
    assert "https://example.invalid/slot-guide" in base.format_context(matches)


def test_empty_or_missing_knowledge_file_returns_no_matches(tmp_path):
    base = KnowledgeBase(tmp_path / "missing.json")

    assert base.search("refund policy") == []
    assert "No relevant entry" in base.format_context([])


def test_search_ignores_malformed_records(tmp_path):
    path = tmp_path / "knowledge_base.json"
    path.write_text(json.dumps({
        "faq": ["not a record", {"question": "ticket support", "answer": "Open a ticket."}],
        "rules": "invalid section"
    }), encoding="utf-8")

    matches = KnowledgeBase(path).search("ticket support")

    assert len(matches) == 1
    assert matches[0]["answer"] == "Open a ticket."
