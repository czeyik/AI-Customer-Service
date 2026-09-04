from app.services.retrieval import RetrievedChunk


class ApprovedKnowledgeResponder:
    """Render a deterministic answer from approved retrieved knowledge.

    This remains the safe response path until the hosted model provider adapter is implemented.
    """

    def generate(self, language: str, chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            return localized_unsure(language)
        core = chunks[0].content.strip()
        if language == "ms":
            return (
                "Ini yang saya temui dalam maklumat sokongan DUDU Car yang diluluskan: "
                f"{core}\n\nSaya harap ini membantu. Jika belum menjawab soalan anda, "
                "saya boleh bantu membuat tiket sokongan."
            )
        if language == "zh":
            return (
                "这是我从 DUDU Car 已批准的客服资料中找到的信息："
                f"{core}\n\n希望这能帮到你。如果仍未解决你的问题，我可以帮你创建客服工单。"
            )
        return (
            "Here’s what I found in DUDU Car's approved support information: "
            f"{core}\n\nI hope this helps. If it does not answer your question, "
            "I can help create a support ticket."
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
