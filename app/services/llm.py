from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.services.language import language_name
from app.services.retrieval import RetrievedChunk


@dataclass(frozen=True)
class LLMResult:
    text: str
    used_model: bool


class LLMClient:
    def generate_support_answer(
        self, user_text: str, language: str, chunks: list[RetrievedChunk]
    ) -> LLMResult:
        settings = get_settings()
        if settings.llm_enabled and settings.llm_provider == "ollama":
            result = self._generate_with_ollama(user_text, language, chunks)
            if result:
                return LLMResult(text=result, used_model=True)
        return LLMResult(text=self._fallback_answer(language, chunks), used_model=False)

    def _generate_with_ollama(
        self, user_text: str, language: str, chunks: list[RetrievedChunk]
    ) -> str | None:
        settings = get_settings()
        context = "\n\n".join(f"Source: {chunk.source_title}\n{chunk.content}" for chunk in chunks)
        prompt = (
            "You are the DUDU Car support assistant. Answer only using approved context "
            "and safe general customer-service reasoning. Do not perform refunds, cancellations, "
            "account changes, or reveal private data. If uncertain, say so and offer a ticket.\n\n"
            f"Respond in {language_name(language)}.\n\n"
            f"Approved context:\n{context}\n\n"
            f"User message:\n{user_text}\n\nSupport answer:"
        )
        try:
            response = httpx.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
                timeout=15,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        answer = response.json().get("response")
        if not isinstance(answer, str) or not answer.strip():
            return None
        return answer.strip()

    def _fallback_answer(self, language: str, chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            return localized_unsure(language)
        core = chunks[0].content.strip()
        if language == "ms":
            return (
                "Berdasarkan maklumat sokongan DUDU Car yang diluluskan: "
                f"{core}\n\nJika ini tidak menjawab soalan anda, saya boleh bantu buat tiket sokongan."
            )
        if language == "zh":
            return (
                "根据 DUDU Car 已批准的客服资料："
                f"{core}\n\n如果这没有解决你的问题，我可以帮你创建客服工单。"
            )
        return (
            "Based on DUDU Car's approved support information: "
            f"{core}\n\nIf this does not answer your question, I can help create a support ticket."
        )


def localized_unsure(language: str) -> str:
    if language == "ms":
        return (
            "Maaf, saya belum cukup pasti berdasarkan maklumat sokongan yang diluluskan. "
            "Saya boleh bantu buat tiket supaya pasukan sokongan menyemaknya."
        )
    if language == "zh":
        return "抱歉，我无法从已批准的客服资料中确认答案。我可以帮你创建客服工单，让客服团队跟进。"
    return (
        "I am not fully sure based on the approved support information. "
        "I can help create a ticket so the support team can review it."
    )

