"""Model factory. Local Ollama by default; OpenAI is opt-in (used for eval comparisons)."""

from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.config import get_settings


def get_chat_model(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    s = get_settings()
    provider = provider or s.llm_provider
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model or s.ollama_model,
            base_url=s.ollama_base_url,
            temperature=0,
            num_ctx=8192,
            # Qwen3.5 "thinking" adds latency without helping short, schema-guided tasks.
            reasoning=False,
            keep_alive="30m",
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not s.openai_api_key or not (model or s.openai_model):
            raise ValueError("Set OPENAI_API_KEY and OPENAI_MODEL in .env to use the OpenAI provider")
        return ChatOpenAI(model=model or s.openai_model, api_key=s.openai_api_key)
    raise ValueError(f"Unknown LLM provider: {provider}")


class NomicEmbeddings(Embeddings):
    """nomic-embed-text expects a task prefix. Everything we embed is symmetric
    (term vs. node name, question vs. example question), so both sides share one."""

    PREFIX = "clustering: "

    def __init__(self) -> None:
        from langchain_ollama import OllamaEmbeddings

        s = get_settings()
        self._inner = OllamaEmbeddings(model=s.embed_model, base_url=s.ollama_base_url)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_documents([self.PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(self.PREFIX + text)


class SapBERTEmbeddings(Embeddings):
    """SapBERT (Liu et al., NAACL 2021): PubMedBERT fine-tuned with self-alignment pretraining on
    UMLS synonyms, so "heart attack" and "myocardial infarction" land next to each other. It is
    built for exactly this job - biomedical entity linking - which general embedders do poorly.
    Uses the [CLS] vector, as in the model card. Runs on Apple-silicon GPU (MPS) when available."""

    MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"

    def __init__(self, batch_size: int = 128) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL)
        self.model = AutoModel.from_pretrained(self.MODEL).to(self.device).eval()
        self.batch_size = batch_size

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with self._torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                batch = self.tokenizer(
                    texts[i : i + self.batch_size],
                    padding=True,
                    truncation=True,
                    max_length=32,
                    return_tensors="pt",
                ).to(self.device)
                cls = self.model(**batch).last_hidden_state[:, 0, :]
                out += cls.float().cpu().tolist()
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@lru_cache
def get_embeddings() -> Embeddings:
    """Sentence embeddings (few-shot example retrieval)."""
    return NomicEmbeddings()


@lru_cache
def get_entity_embeddings(kind: str | None = None) -> Embeddings:
    """Entity-name embeddings for linking: SapBERT by default, nomic kept for comparison."""
    kind = kind or get_settings().entity_embedder
    return SapBERTEmbeddings() if kind == "sapbert" else NomicEmbeddings()
