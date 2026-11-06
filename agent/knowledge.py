"""Small deterministic lexical retriever; documents are synthetic demo guidance."""
import json
from pathlib import Path


class KnowledgeBase:
    def __init__(self, path=None):
        path = path or Path(__file__).with_name("manuals.json")
        self.documents = json.loads(Path(path).read_text(encoding="utf-8"))

    def search(self, query, limit=3):
        terms = set(query.lower().split())
        matches = []
        for doc in self.documents:
            score = len(terms & set(doc["tags"]))
            if score:
                matches.append({**doc, "score": score})
        return sorted(matches, key=lambda x: (-x["score"], x["document_id"]))[:limit]
