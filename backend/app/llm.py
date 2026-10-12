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


@lru_cache
def get_embeddings() -> Embeddings:
    return NomicEmbeddings()
