import json

from app.services.answer_generation import deterministic_answer, grounded_answer
from app.services.retrieval import RetrievedChunk


CHUNKS = [
    RetrievedChunk(
        content="Fare estimates can change because of distance and traffic.",
        source_title="Fares",
        language="en",
        score=1.0,
    )
]


def test_grounded_answer_uses_only_approved_chunks() -> None:
    answer = "Fare estimates can change because of distance and traffic."
    assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), CHUNKS) == answer
    answer = "No problem at all. Fare estimates can change."
    assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), CHUNKS) == answer


def test_declared_sentence_ending_citations_are_removed_without_hiding_facts() -> None:
    for answer, expected in (
        ("Fare estimates can change. [1]", "Fare estimates can change."),
        ("Fare estimates can change! 【1】\n\nCheck before booking.",
         "Fare estimates can change!\n\nCheck before booking."),
        ("预估车费可能变化。[1]", "预估车费可能变化。"),
    ):
        assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), CHUNKS) == expected
    for unsupported in (
        "Fare estimates can change. [999]",
        "Fare estimates can change. [2]",
        "The fare is RM 1. [1]",
        "Call 999. [1]",
        "There are [1] available rides.",
        "See https://unapproved.example/help. [1]",
    ):
        assert grounded_answer(
            json.dumps({"answer": unsupported, "citations": [1]}), CHUNKS * 2
        ) is None
    source = "The fare is RM 5. See https://duducar.co/help"
    chunks = [RetrievedChunk(content=source, source_title="Support", language="en", score=1)]
    answer = "The fare is RM 5. [1]\nSee https://duducar.co/help"
    assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), chunks) == source.replace(" See", "\nSee")
    # A URL suffix resembling a citation must not be rewritten into an approved URL.
    answer = "See https://duducar.co/help.[1]"
    assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), chunks) is None


def test_ungrounded_numbers_citations_and_promises_are_rejected() -> None:
    for response in (
        {"answer": "The fare is guaranteed for 30 days.", "citations": [1]},
        {"answer": "We guarantee fare estimates.", "citations": [1]},
        {"answer": "Fare estimates change.", "citations": [2]},
        {"answer": "Fare estimates change.", "citations": [True]},
        [],
    ):
        assert grounded_answer(json.dumps(response), CHUNKS) is None


def test_overlap_does_not_allow_new_eligibility_prices_or_universal_promises():
    chunks = [
        RetrievedChunk(
            content="Support reviews refund requests within 24 hours. Waiting time may be 5 minutes.",
            source_title="Support",
            language="en",
            score=1,
        )
    ]
    for answer in (
        "All refund requests are approved within 24 hours.",
        "The fare is RM 5.",
        "Riders are automatically eligible for refund requests within 24 hours.",
        "No problem at all. All refund requests are approved within 24 hours.",
        "No refund requests are reviewed at all.",
        "No problem at all customers receive refunds.",
    ):
        assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), chunks) is None


def test_automatic_assistant_identity_is_not_an_automatic_action() -> None:
    chunks = [
        RetrievedChunk(
            content=(
                "Penumpang dan pemandu hendaklah memastikan nombor telefon serta "
                "maklumat profil dalam akaun DUDU Car mereka sentiasa terkini. Jika "
                "anda menghadapi masalah log masuk, cuba kemas kini aplikasi, semak "
                "nombor telefon yang digunakan semasa pendaftaran, dan minta kod "
                "pengesahan baharu. Jangan sesekali berkongsi kata laluan, kata laluan "
                "sekali guna (OTP), atau kod akaun peribadi dalam chat."
            ),
            source_title="Accounts and login",
            language="ms",
            score=1,
        )
    ]
    answer = (
        "Atas sebab keselamatan dan privasi, pembantu automatik tidak boleh mengakses "
        "akaun anda secara langsung. Untuk melindungi maklumat peribadi anda, jangan "
        "sesekali berkongsi kata laluan, kata laluan sekali guna (OTP), atau kod akaun "
        "peribadi dalam chat. Sekiranya anda menghadapi masalah, pembantu automatik "
        "boleh membantu menerangkan langkah umum dan membantu membuat tiket sokongan "
        "untuk semakan pegawai."
    )
    assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), chunks) == answer
    for identity in (
        "Pembantu automatik DUDU Car tidak mempunyai akses langsung ke akaun anda — "
        "ini adalah sebahagian daripada langkah keselamatan.",
        "Saya ialah pembantu sokongan automatik DUDU Car.",
        "Saya ialah pembantu automatik DUDU Car, bukan manusia.",
        "Saya adalah pembantu automatik dan tidak mempunyai akses langsung kepada akaun anda.",
        "Sebagai pembantu automatik DUDU Car, saya tidak mempunyai akses terus ke akaun anda.",
        "Pembantu automatik ini tidak mempunyai akses terus ke akaun anda.",
        "Pembantu automatik ini tidak boleh meluluskan atau menolak pemandu.",
    ):
        assert grounded_answer(
            json.dumps({"answer": identity, "citations": [1]}), chunks
        ) == identity
    for unsupported in (
        "Bayaran balik diluluskan secara automatik.",
        "Permohonan diluluskan secara automatik.",
        "Akaun dikemas kini secara automatik.",
        "Pembantu automatik meluluskan bayaran balik.",
        "Pembantu automatik boleh membantu meluluskan bayaran balik.",
        "Pembantu sokongan automatik meluluskan bayaran balik.",
        "Pembantu automatik DUDU Car meluluskan bayaran balik.",
        "Pembantu automatik ini meluluskan bayaran balik.",
        "Pembantu sokongan automatik DUDU Car boleh membantu meluluskan bayaran balik.",
        "Saya ialah pembantu automatik DUDU Car, bukan manusia, dan meluluskan bayaran balik.",
        "Pembantu automatik dan boleh membantu meluluskan bayaran balik.",
        "Sebagai pembantu automatik DUDU Car, saya boleh meluluskan bayaran balik.",
    ):
        assert grounded_answer(
            json.dumps({"answer": unsupported, "citations": [1]}), chunks
        ) is None


def test_outage_answer_is_deterministic_and_approved() -> None:
    assert CHUNKS[0].content in deterministic_answer("en", CHUNKS)


def test_numbered_list_markers_are_not_policy_numbers() -> None:
    from scripts.ingest_seed import corpus_records

    record = next(
        item for item in corpus_records()
        if item["document_key"] == "support-ticket-response-targets"
        and item["language"] == "en"
    )
    chunks = [RetrievedChunk(
        content=record["chunks"][0], source_title=record["title"], language="en", score=1,
    )]
    answer = "1. Normal: 3–5 days.\n2. High: 1–3 days.\n3. Urgent: within 24 hours."
    assert grounded_answer(json.dumps({"answer": answer, "citations": [1]}), chunks) == answer
    unsupported = answer.replace("1–3 days", "2 days")
    assert grounded_answer(json.dumps({"answer": unsupported, "citations": [1]}), chunks) is None
    for unsupported in ("999. Call emergency services.", "1. Normal: 3–5 days.\n999. Call emergency services."):
        assert grounded_answer(json.dumps({"answer": unsupported, "citations": [1]}), chunks) is None
