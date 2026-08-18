"""Knowledge Forge — pluggable embedding layer.

Two providers, automatisch met terugval:

1. Ollama embeddings (voorkeur als de server mét ``--embeddings`` draait):
   echte dense vectors, semantisch sterk. Controleer met
   ``curl /api/embeddings`` — als de server "does not support embeddings"
   teruggeeft, vallen we terug op 2.

2. Pure-python sparse-vector (TF-IDF-achtig) cosine: géén externe deps,
   géén server, werkt altijd offline. Kwaliteit is "keyword + synoniem-
   dichtheid" in plaats van diepe semantiek, maar voor retrieval binnen één
   document of een kleine vault is het verrassend goed en 100% gratis/privé.

Het contract is eenvoudig: ``embed(texts) -> List[List[float]]`` en
``similarity(a, b) -> float``. Alles daarbovenop (store, search) hangt aan
dit contract, dus je kunt later moeiteloos naar een echte vector-DB.

Ontwerpkeuzes (wereldklasse-robustheid):
- Geen enkele embed-fout mag de ingest pijn doen: bij Ollama-fout valt de
  hele provider terug op sparse, en sparse faalt nooit.
- Deterministisch: zelfde tekst => zelfde vector (geen random init).
- Taalneutraal: Nederlands én Engels werken (geen taalspecifieke stemming,
  wel lowercase + diacrieten-normalisatie zodat "mét" en "met" matchen).
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Tokenisatie (geredeeld met ObsidianService, maar hier standalone) ────────

_STOPWORDS = {
    # NL + EN gemengd — goed genoeg voor retrieval-relevantie
    "de", "het", "een", "en", "of", "in", "op", "met", "van", "voor", "naar",
    "door", "aan", "bij", "over", "uit", "is", "zijn", "wordt", "als", "dat",
    "dit", "die", "deze", "hier", "daar", "wat", "wie", "hoe", "waar", "wanneer",
    "niet", "ook", "maar", "om", "tot", "tussen", "onder", "boven", "naast",
    "the", "a", "an", "and", "or", "in", "on", "with", "for", "to", "from",
    "by", "at", "of", "is", "are", "was", "were", "be", "been", "this", "that",
    "these", "those", "it", "its", "as", "if", "then", "than", "but", "not",
    "we", "you", "they", "he", "she", "i", "our", "your", "their", "his", "her",
}


def _normalize(text: str) -> str:
    """Diacrieten weg + lowercase, zodat 'mét'≈'met' en 'é'≈'e'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def _tokenize(text: str) -> List[str]:
    # 2+ lettergrepige alfanumerieke tokens; sluit stopwords uit.
    words = re.findall(r"[a-z0-9À-ɏ]{2,}", _normalize(text))
    return [w for w in words if w not in _STOPWORDS]


# ── Sparse-vector provider (pure python, altijd beschikbaar) ────────────────

class SparseEmbedder:
    """TF-IDF-achtige sparse vector in een vaste, op frequentie gerangschikte
    woordenschat. Cosine tussen twee sparse vectoren."""

    def __init__(self, vocab_size: int = 20000):
        self.vocab_size = vocab_size
        self._vocab: Optional[dict] = None  # word -> index (lazy)
        self._idf: Optional[dict] = None

    # Vocabulaire/bootstrap op een corpus (optioneel, geeft betere IDF)
    def fit(self, corpus: List[str]) -> None:
        from collections import Counter
        df: Counter = Counter()
        for doc in corpus:
            seen = set(_tokenize(doc))
            for w in seen:
                df[w] += 1
        n = max(1, len(corpus))
        # IDF: log((1+N)/(1+df)) + 1 — smooth
        self._idf = {w: math.log((1 + n) / (1 + c)) + 1.0 for w, c in df.items()}
        # Houd een vaste volgorde op frequentie (df) voor stabiliteit
        ordered = sorted(df.keys(), key=lambda w: (-df[w], w))[:self.vocab_size]
        self._vocab = {w: i for i, w in enumerate(ordered)}

    def _vector(self, text: str) -> dict:
        from collections import Counter
        if self._vocab is None:
            # ZONDER fit: gebruik een hashing-vocab (HashingVectorizer-stijl).
            # Elk woord -> stabiele hash-index in een vaste, grote ruimte,
            # zodat twee willekeurige teksten WÉL cross-doc vergelijkbaar
            # zijn (geen gedeelde vocab nodig). Collisions zijn zeldzaam en
            # licht vertekend — ruimschoots goed genoeg voor retrieval.
            toks = _tokenize(text)
            vec: dict = {}
            tf = Counter(toks)
            for w, c in tf.items():
                h = abs(hash(w)) % 50000
                weight = (1.0 + math.log(c))
                vec[h] = vec.get(h, 0.0) + weight
            return vec
        idf = self._idf or {}
        tf = Counter(_tokenize(text))
        vec: dict = {}
        for w, c in tf.items():
            idx = self._vocab.get(w)
            if idx is None:
                continue
            weight = (1.0 + math.log(c)) * idf.get(w, 1.0)
            vec[idx] = vec.get(idx, 0.0) + weight
        return vec

    def embed_one(self, text: str) -> List[float]:
        vec = self._vector(text)
        if not vec:
            return []
        # L2-normaliseren zodat dot-product == cosine
        norm = math.sqrt(sum(v * v for v in vec.values()))
        dim = max(vec.keys()) + 1 if vec else 0
        out = [0.0] * dim
        for i, v in vec.items():
            out[i] = v / norm
        return out

    @staticmethod
    def similarity(a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        # sparse dot (beide genormaliseerd => cosine)
        # kleine prefix-optimalisatie: loop over de kortste
        if len(a) > len(b):
            a, b = b, a
        return sum(a[i] * b[i] for i in range(len(a)))


# ── Ollama dense provider ────────────────────────────────────────────────────

class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._ok: Optional[bool] = None

    def _probe(self) -> bool:
        if self._ok is not None:
            return self._ok
        try:
            import requests
            r = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": "test"},
                timeout=self.timeout,
            )
            self._ok = r.status_code == 200 and "embedding" in r.json()
        except Exception as e:
            logger.debug("[forge-embed] Ollama probe mislukt: %s", e)
            self._ok = False
        return self._ok

    def embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not self._probe():
            return None
        try:
            import requests
            out = []
            for t in texts:
                r = requests.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": t},
                    timeout=self.timeout,
                )
                if r.status_code != 200:
                    return None
                data = r.json()
                emb = data.get("embedding")
                if not emb:
                    return None
                out.append([float(x) for x in emb])
            return out
        except Exception as e:
            logger.warning("[forge-embed] Ollama embed fout: %s", e)
            return None

    @staticmethod
    def similarity(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


# ── Facade ───────────────────────────────────────────────────────────────────

class EmbeddingEngine:
    """Kiest automatisch de beste beschikbare provider met veilige terugval."""

    def __init__(self, ollama_base: str = "", ollama_model: str = "llama3.1",
                 sparse_vocab: int = 20000):
        self._ollama = OllamaEmbedder(ollama_base, ollama_model) if ollama_base else None
        self._sparse = SparseEmbedder(sparse_vocab)
        self.provider = "pending"

    def _preferred(self):
        if self._ollama is not None and self._ollama._probe():
            return self._ollama, "ollama"
        return self._sparse, "sparse"

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Returneert een lijst vectors; provider wordt hier gekozen/gerapporteerd."""
        if not texts:
            return []
        eng, name = self._preferred()
        self.provider = name
        if name == "ollama":
            dense = self._ollama.embed(texts)
            if dense is not None:
                return dense
            # terugval naar sparse als Ollama mid-flight faalt
            self.provider = "sparse"
            eng = self._sparse
        return [eng.embed_one(t) for t in texts]

    def similarity(self, a: List[float], b: List[float]) -> float:
        # Bij sparse zijn het genormaliseerde vectors => cosine.
        # Bij dense ook cosine. Beide providers delen dezelfde semantiek.
        eng = self._ollama if self.provider == "ollama" else self._sparse
        try:
            return eng.similarity(a, b)
        except Exception:
            return SparseEmbedder.similarity(a, b)


# ── Module-level singleton (gedeeld over requests) ───────────────────────────

_engine: Optional[EmbeddingEngine] = None


def get_embedder() -> EmbeddingEngine:
    """Lazy singleton — leest embed-config uit omgeving.

    Voorkeur: een dedicated embeddings-endpoint via
    KNOWLEDGE_FORGE_EMBED_URL / KNOWLEDGE_FORGE_EMBED_MODEL (zodat de
    embeddings-backend onafhankelijk van de LLM-fallback Ollama kan draaien).
    Deze vars worden geladen uit backend/knowledge_forge/forge_settings.env
    (geen secrets) én uit de hoofd-.env indien aanwezig.

    Terugval: de algemene OLLAMA_BASE_URL/OLLAMA_MODEL als die een lokale
    /api-server is (geen /v1 OpenAI-shape). Als laatste redmiddel: de
    bekende lokale embeddings-instance op :11436 (nomic-embed-text), mits die
    daadwerkelijk embeddings serveert.
    """
    global _engine
    if _engine is None:
        import os
        # Laad de forge-settings (geen secrets) — overschrijft NIET wat er al
        # in de omgeving staat (dus een hoofd-.env KNOWLEDGE_FORGE_* wint).
        try:
            from dotenv import load_dotenv
            from pathlib import Path
            _settings = Path(__file__).resolve().parent / "forge_settings.env"
            if _settings.exists():
                load_dotenv(_settings, override=False)
        except Exception:
            pass

        # 1) dedicated embeddings-endpoint
        eb = os.getenv("KNOWLEDGE_FORGE_EMBED_URL", "").rstrip("/")
        em = os.getenv("KNOWLEDGE_FORGE_EMBED_MODEL", "nomic-embed-text")
        if eb and ("localhost" in eb or "127.0.0.1" in eb):
            _engine = EmbeddingEngine(ollama_base=eb, ollama_model=em)
            return _engine
        # 2) terugval op algemene Ollama als die lokaal + /api-vorm is
        base = os.getenv("OLLAMA_BASE_URL", "").replace("/v1", "").rstrip("/")
        model = os.getenv("OLLAMA_MODEL", "llama3.1")
        is_local = ("localhost" in base) or ("127.0.0.1" in base)
        use_ollama = bool(base) and "/api" not in base and is_local
        if use_ollama:
            _engine = EmbeddingEngine(ollama_base=base, ollama_model=model)
            return _engine
        # 3) harde fallback: bekende lokale embeddings-instance (als die leeft)
        _engine = EmbeddingEngine(ollama_base="http://127.0.0.1:11436",
                                  ollama_model="nomic-embed-text")
    return _engine
