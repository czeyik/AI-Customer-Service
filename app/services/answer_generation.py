import json
import re

from app.services.retrieval import RetrievedChunk


PRIVACY_NOTICE_URL = "https://duducar.co/privacy-notice"
UNSAFE_OUTPUT_PHRASES = (
    "i guarantee", "we guarantee", "i promise", "we promise", "has been refunded",
    "has been booked", "has been cancelled", "i accessed your account", "i created your ticket",
    "i have created", "has been created", "i am a human", "saya telah membuat tiket",
    "工单已创建", "已经退款", "已经取消", "saya jamin", "kami jamin", "telah dibayar balik",
    "telah ditempah", "telah dibatalkan", "我保证", "我们保证", "已退款", "已预订", "已取消",
    "我是人工客服",
)


def grounded_answer(raw: str, chunks: list[RetrievedChunk]) -> str | None:
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or set(result) != {"answer", "citations"}:
        return None
    answer, citations = result["answer"], result["citations"]
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 1600:
        return None
    if not isinstance(citations, list) or not citations or any(
        type(item) is not int or item < 1 or item > len(chunks) for item in citations
    ):
        return None

    # ponytail: normalize only sentence-ending citation markers; richer citation
    # syntax needs an explicit parser so ordinary bracketed numbers stay facts.
    marker = r"(?<=[.!?。！？])[ \t]*(?:\[(\d+)\]|【(\d+)】)(?=\s|$)"
    if any(int(match[0] or match[1]) not in citations for match in re.findall(marker, answer)):
        return None
    normalized_answer = re.sub(marker, "", answer)
    if _urls(normalized_answer) != _urls(answer):
        return None
    answer = normalized_answer
    context = " ".join(chunks[index - 1].content for index in citations)
    if not _numbers(answer) <= _numbers(context) or not _urls(answer) <= _urls(context):
        return None
    if any(phrase in answer.lower() for phrase in UNSAFE_OUTPUT_PHRASES):
        return None
    qualifiers = r"\b(?:all|every|always|never|automatically|eligible|entitled|guaranteed|semua|sentiasa|automatik)\b|所有|一定|自动批准|保证"
    safe_identity = (
        r"\b(?:"
        r"pembantu (?:ai |sokongan )?automatik(?: dudu car)?(?: ini)?"
        r"(?=(?: (?:dan )?|, saya )(?:tidak (?:boleh|mempunyai akses)|boleh membantu (?:menerangkan|menjelaskan))\b)"
        r"|saya (?:(?:ialah|adalah) )?pembantu (?:ai |sokongan )?automatik dudu car"
        r"(?=(?:, bukan manusia)?\s*[.!?](?:\s|$))"
        r"|no problem at all(?=\s*[.!?](?:\s|$))"
        r")"
    )
    answer_qualifiers = set(re.findall(qualifiers, re.sub(safe_identity, "pembantu", answer.lower())))
    context_qualifiers = set(re.findall(qualifiers, re.sub(safe_identity, "pembantu", context.lower())))
    if answer_qualifiers - context_qualifiers:
        return None
    money = r"(?:RM|MYR|USD|\$)\s*\d+(?:[.,]\d+)?"
    if {re.sub(r"\s", "", value).lower() for value in re.findall(money, answer, re.I)} - {
        re.sub(r"\s", "", value).lower() for value in re.findall(money, context, re.I)
    }:
        return None
    return answer.strip()


def deterministic_answer(language: str, chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return localized_unsure(language)
    return {
        "en": "Here’s what I found in DUDU Car's approved support information: ",
        "ms": "Ini maklumat sokongan DUDU Car yang diluluskan: ",
        "zh": "这是 DUDU Car 已批准的客服资料：",
    }[language] + chunks[0].content.strip()


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


def render_control_prompt(language: str, purpose: str, field: str | None = None) -> str:
    if purpose == "field" and field == "description":
        return {
            "en": "What happened, and what would you like our support team to help with?",
            "ms": "Apakah yang berlaku, dan apakah bantuan yang anda perlukan daripada pasukan sokongan?",
            "zh": "发生了什么？你希望客服团队帮助处理什么问题？",
        }[language]
    if purpose == "review":
        return {
            "en": "Please review these ticket details. Reply Submit to send, or tell me what to correct.",
            "ms": "Sila semak butiran tiket ini. Balas Hantar untuk menghantar, atau nyatakan pembetulan.",
            "zh": "请检查工单资料。回复“提交”发送，或告诉我需要更正的内容。",
        }[language]
    if purpose == "case_confirmation":
        return {
            "en": "Reply Yes to confirm the proposed case update, or Stop to cancel.",
            "ms": "Balas Ya untuk mengesahkan kemas kini kes, atau Berhenti untuk membatalkan.",
            "zh": "回复“同意”确认工单补充，或“停止”取消。",
        }[language]
    prompts = {
        "en": {
            ("consent", None): f"To arrange human follow-up, may we store your issue details in a support ticket? Reply Yes or No. Privacy Notice: {PRIVACY_NOTICE_URL}",
            ("field", "name"): "Great, thank you! May I have your name for the support ticket?",
            ("field", "email"): "Thanks! What valid email address should our support team use for this ticket?",
            ("field", "phone_number"): "Thank you! Please share the WhatsApp phone number you would like us to use for follow-up.",
            ("details", "ride_details"): "We’re almost done! Please share the available trip ID, date/time, pickup location and destination. You may send the details across multiple messages and attach supporting pictures or videos. Reply Done after submitting everything, or Skip if no ride details apply.",
            ("details", None): "Do you have any other relevant details or supporting pictures or videos? Send them now, or reply Done if you have submitted everything needed.",
        },
        "ms": {
            ("consent", None): f"Untuk mengatur susulan oleh pegawai, bolehkah kami menyimpan butiran isu anda dalam tiket sokongan? Balas Ya atau Tidak. Notis Privasi: {PRIVACY_NOTICE_URL}",
            ("field", "name"): "Baik, terima kasih! Boleh saya dapatkan nama anda untuk tiket sokongan?",
            ("field", "email"): "Terima kasih! Apakah alamat e-mel sah yang patut digunakan oleh pasukan sokongan kami untuk tiket ini?",
            ("field", "phone_number"): "Terima kasih! Sila berikan nombor telefon WhatsApp yang anda mahu kami gunakan untuk susulan.",
            ("details", "ride_details"): "Kita hampir selesai! Sila kongsikan ID perjalanan, tarikh/masa, lokasi pengambilan dan destinasi yang tersedia. Anda boleh menghantar butiran dalam beberapa mesej dan melampirkan gambar atau video sokongan. Balas Selesai selepas menghantar semuanya, atau Langkau jika tiada butiran perjalanan berkaitan.",
            ("details", None): "Adakah anda mempunyai butiran lain atau gambar atau video sokongan? Hantar sekarang, atau balas Selesai jika semua maklumat yang diperlukan telah dihantar.",
        },
        "zh": {
            ("consent", None): f"为了安排人工客服跟进，你是否同意我们将问题资料保存至客服工单？请回复同意或不同意。隐私声明：{PRIVACY_NOTICE_URL}",
            ("field", "name"): "好的，谢谢！可以告诉我用于客服工单的姓名吗？",
            ("field", "email"): "谢谢！我们的客服团队应使用哪个有效电子邮箱地址跟进此工单？",
            ("field", "phone_number"): "谢谢！请提供你希望我们用于后续联系的 WhatsApp 电话号码。",
            ("details", "ride_details"): "我们快完成了！请提供现有的行程编号、日期/时间、上车地点和目的地。你可以分多条消息发送资料，并附上相关图片或视频。全部提交后请回复“完成”；如无相关行程资料，请回复“跳过”。",
            ("details", None): "你还有其他相关资料或支持图片、视频吗？请现在发送；如果所需资料已全部提交，请回复“完成”。",
        },
    }
    try:
        return prompts[language][purpose, field]
    except KeyError as exc:
        raise ValueError(f"unsupported control prompt: {purpose}/{field}") from exc


def render_ticket_review(language: str, fields: dict, *, proposed_priority: str | None = None) -> str:
    labels = {
        "en": ("Name", "Email", "Contact phone", "Issue", "Trip ID", "Ride details"),
        "ms": ("Nama", "E-mel", "Telefon hubungan", "Isu", "ID perjalanan", "Butiran perjalanan"),
        "zh": ("姓名", "邮箱", "联系电话", "问题", "行程编号", "行程详情"),
    }[language]
    answer = render_control_prompt(language, "review") + "\n" + "\n".join(
        f"{label}: {fields[key]}"
        for label, key in zip(
            labels, ("name", "email", "phone_number", "description", "trip_id", "ride_details")
        )
        if fields.get(key)
    )
    if proposed_priority:
        answer += "\n" + {
            "en": f"Proposed priority: {proposed_priority}",
            "ms": f"Keutamaan dicadangkan: {proposed_priority}",
            "zh": f"拟定优先级：{proposed_priority}",
        }[language]
    return answer


def render_ticket_declined(language: str) -> str:
    return {
        "en": "No problem. I have not created a ticket, and I’m still here if you would like general support information.",
        "ms": "Tiada masalah. Saya tidak membuat tiket, dan saya masih sedia membantu jika anda memerlukan maklumat sokongan umum.",
        "zh": "没问题，我没有创建工单。如果你需要一般客服信息，我仍然很乐意协助。",
    }[language]


def render_ticket_receipt(
    language: str, public_id: str, urgency: str, *, support_is_open: bool
) -> str:
    priority = {
        "en": {"normal": "normal", "high": "high", "urgent": "urgent"},
        "ms": {"normal": "biasa", "high": "tinggi", "urgent": "segera"},
        "zh": {"normal": "普通", "high": "高", "urgent": "紧急"},
    }[language][urgency]
    target = {
        "en": {"normal": "3–5 days", "high": "1–3 days", "urgent": "within 24 hours"},
        "ms": {"normal": "3–5 hari", "high": "1–3 hari", "urgent": "dalam 24 jam"},
        "zh": {"normal": "3–5 天", "high": "1–3 天", "urgent": "24 小时内"},
    }[language][urgency]
    text = {
        "en": f"{'Your urgent ticket' if urgency == 'urgent' else 'All set—ticket'} {public_id} has been created. Priority: {priority}. First human response target: {target}. This is not a resolution promise. Human support is available 9:00 AM–6:00 PM every day, Malaysia time.",
        "ms": f"{'Tiket segera' if urgency == 'urgent' else 'Selesai—tiket'} {public_id} telah dibuat. Keutamaan: {priority}. Sasaran respons pertama oleh pegawai: {target}. Ini bukan janji penyelesaian. Sokongan manusia tersedia setiap hari, 9:00 pagi–6:00 petang waktu Malaysia.",
        "zh": f"{'紧急工单' if urgency == 'urgent' else '已经办好—工单'} {public_id} 已创建。优先级：{priority}。人工首次回复目标：{target}。这并非解决时限承诺。人工客服时间为马来西亚时间每天上午 9:00 至下午 6:00。",
    }[language]
    if not support_is_open:
        text += {
            "en": " Your ticket is now in the queue for the next human-support window.",
            "ms": " Tiket anda kini berada dalam barisan untuk waktu sokongan manusia seterusnya.",
            "zh": " 你的工单现已排入下一个人工客服时段。",
        }[language]
    return text


def render_case_confirmation(language: str, public_id: str, *, reopen: bool) -> str:
    return {
        "en": f"Add this update to {public_id}" + (" and reopen it" if reopen else "") + "? Reply Yes to confirm, or Stop to cancel.",
        "ms": f"Tambah kemas kini ini pada {public_id}" + (" dan buka semula kes" if reopen else "") + "? Balas Ya untuk mengesahkan, atau Berhenti untuk membatalkan.",
        "zh": f"将这次补充加入 {public_id}" + ("并重新开启工单" if reopen else "") + "？回复“同意”确认，或“停止”取消。",
    }[language]


def render_case_receipt(language: str) -> str:
    return {
        "en": "Your case update has been recorded.",
        "ms": "Kemas kini kes anda telah direkodkan.",
        "zh": "你的工单补充已记录。",
    }[language]


def _numbers(text: str) -> set[str]:
    # ponytail: only 1..N multiline lists are formatting; parse Markdown if richer lists are needed.
    marker = r"(?m)^[ \t]*(\d+)[.)][ \t]+"
    markers = re.findall(marker, text)
    if len(markers) > 1 and markers == [str(i) for i in range(1, len(markers) + 1)]:
        text = re.sub(marker, "", text)
    return set(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))


def _urls(text: str) -> set[str]:
    return set(re.findall(r"https?://\S+", text))
