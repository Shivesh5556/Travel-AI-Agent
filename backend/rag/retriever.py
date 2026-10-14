"""
The RAG bit: retrieval augmented generation.

What it does: we keep a small library of travel tips written by actual
travellers (gems.json). When you plan a trip, we FIND the tips that match
your destination and interests, and we FEED them to the LLM as context. So
the itinerary can suggest things that are not on any booking site.

How the retrieval works: TF-IDF plus cosine similarity, written from scratch
in about 60 lines. No numpy, no torch, no vector database.

  TF  (term frequency)          - how often a word appears in one document
  IDF (inverse document freq)   - how rare that word is across all documents
  TF x IDF                      - words that are common HERE but rare
                                  everywhere else describe this document best
  cosine similarity             - the angle between two of these vectors;
                                  smaller angle means more similar

This is real sparse retrieval, the same family as BM25 that search engines
used for decades. It is not embeddings. If you want to upgrade it later,
swap this file for sentence-transformers + pgvector and nothing else in the
project has to change - that is why retrieval lives behind one function.
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

CORPUS_PATH = Path(__file__).parent / "gems.json"

# Words too common to be useful for matching.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up", "about", "into",
    "it", "its", "this", "that", "these", "those", "you", "your", "if", "as", "so",
    "not", "no", "do", "does", "did", "can", "will", "just", "very", "there", "here",
}


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords and single letters."""
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


class GemRetriever:
    """
    Loads the corpus once at startup and builds the TF-IDF index in memory.
    With a few dozen documents this takes microseconds.
    """

    def __init__(self, corpus_path: Path = CORPUS_PATH):
        self.documents: list[dict] = []
        self.doc_vectors: list[dict[str, float]] = []
        self.idf: dict[str, float] = {}
        self._load(corpus_path)

    def _load(self, path: Path):
        if not path.exists():
            print("[rag] gems.json not found, retrieval disabled")
            return

        self.documents = json.loads(path.read_text(encoding="utf-8"))

        # Everything we know about a gem becomes searchable text.
        tokenized = []
        for doc in self.documents:
            blob = " ".join([
                doc.get("city", ""),
                doc.get("title", ""),
                doc.get("text", ""),
                " ".join(doc.get("tags", [])),
            ])
            tokenized.append(tokenize(blob))

        # --- IDF: how rare is each word across the whole corpus? ---
        total_docs = len(tokenized)
        appears_in = Counter()
        for words in tokenized:
            for word in set(words):
                appears_in[word] += 1

        # +1 everywhere so we never divide by zero (this is "smoothed IDF")
        self.idf = {
            word: math.log((total_docs + 1) / (count + 1)) + 1.0
            for word, count in appears_in.items()
        }

        # --- TF-IDF vector for each document, normalised to unit length ---
        self.doc_vectors = [self._vector(words) for words in tokenized]
        print(f"[rag] indexed {total_docs} travel tips")

    def _vector(self, words: list[str]) -> dict[str, float]:
        """Build one normalised TF-IDF vector. Stored as a dict = sparse vector."""
        if not words:
            return {}

        counts = Counter(words)
        vector = {w: (c / len(words)) * self.idf.get(w, 1.0) for w, c in counts.items()}

        # normalise so document length does not affect the score
        magnitude = math.sqrt(sum(v * v for v in vector.values()))
        if magnitude == 0:
            return {}
        return {w: v / magnitude for w, v in vector.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        """
        Dot product of two already-normalised vectors = cosine similarity.
        We loop over the shorter one because most words will not overlap.
        """
        if len(a) > len(b):
            a, b = b, a
        return sum(value * b.get(word, 0.0) for word, value in a.items())

    # A tip from a DIFFERENT city has to be this relevant on its own merits
    # before we will show it. Without this, a Dehradun trip was being shown
    # "eat at the langar in Amritsar" just because both mention food - the
    # city bonus lifted same-city tips but nothing held the others back.
    OTHER_CITY_THRESHOLD = 0.18

    def search(self, query: str, city: str = "", top_k: int = 5,
               min_score: float = 0.02) -> list[dict]:
        """
        Find the most relevant tips.

        Two rules working together:
          1. tips from the right city get a bonus, because "a quiet cafe" in
             Goa is useless if you are going to Manali
          2. tips from anywhere else must clear a much higher bar on their own
             similarity, and get tagged so the UI can label them honestly
        """
        if not self.documents:
            return []

        query_vector = self._vector(tokenize(query + " " + city))
        if not query_vector:
            return []

        city_key = city.strip().lower()
        scored = []

        for i, doc_vector in enumerate(self.doc_vectors):
            raw_score = self._cosine(query_vector, doc_vector)
            doc_city = self.documents[i].get("city", "").lower()

            same_city = bool(city_key) and (doc_city == city_key or city_key in doc_city)

            if same_city:
                score = raw_score + (0.35 if doc_city == city_key else 0.15)
            else:
                # No bonus, and it has to be genuinely relevant to survive.
                if city_key and raw_score < self.OTHER_CITY_THRESHOLD:
                    continue
                score = raw_score

            if score >= min_score:
                scored.append((score, i, same_city))

        # Same-city tips always come first, then by score.
        scored.sort(key=lambda row: (row[2], row[0]), reverse=True)

        # If we have anything at all for the real destination, show only
        # those. A tip about Amritsar on a Dehradun trip is noise even when
        # it is correctly labelled - better to show one relevant tip than
        # five that are padding.
        local = [row for row in scored if row[2]]
        if local:
            scored = local

        results = []
        for score, i, same_city in scored[:top_k]:
            gem = dict(self.documents[i])
            gem["score"] = round(score, 3)
            gem["same_city"] = same_city
            results.append(gem)

        return results

    def as_prompt_context(self, gems: list[dict]) -> str:
        """Format the retrieved tips so we can paste them into the LLM prompt."""
        if not gems:
            return "No local tips found for this destination."
        lines = [f"- {g['title']} ({g['city']}): {g['text']}" for g in gems
                 if g.get("same_city", True)]
        if not lines:
            return "No local tips found for this destination."
        return "Local tips from travellers:\n" + "\n".join(lines)


# One shared instance for the whole app.
retriever = GemRetriever()
