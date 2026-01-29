from dataclasses import dataclass
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


@dataclass
class RAGResult:
    top_chunks: list
    scores: list


class LocalRAG:
    def __init__(self, kb_text: str, chunk_size: int = 400):
        self.chunks = self._chunk(kb_text, chunk_size)
        self.vec = TfidfVectorizer(max_features=4000)
        self.mat = self.vec.fit_transform(self.chunks)

    def _chunk(self, text: str, chunk_size: int):
        text = text.replace("\r\n", "\n")
        parts = []
        cur = ""
        for line in text.split("\n"):
            if len(cur) + len(line) + 1 > chunk_size:
                if cur.strip():
                    parts.append(cur.strip())
                cur = line + "\n"
            else:
                cur += line + "\n"
        if cur.strip():
            parts.append(cur.strip())
        return parts if parts else [text[:chunk_size]]

    def retrieve(self, query: str, k: int = 3) -> RAGResult:
        qv = self.vec.transform([query])
        sims = (self.mat @ qv.T).toarray().ravel()
        idx = np.argsort(-sims)[:k]
        return RAGResult([self.chunks[i] for i in idx], [float(sims[i]) for i in idx])
