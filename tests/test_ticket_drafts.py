from datetime import datetime

import pytest

from app.schemas import ChatRequest
from app.services.ticket_drafts import (
    accept_ticket_offer,
    DialogueData,
    build_field_references,
    consent_answer,
    extract_customer_fields,
    is_done,
    is_explicit_withdrawal,
    is_new_draft_control,
    is_skip,
    make_prompt,
    new_draft,
    owned_case_view,
    prepare_ticket_offer,
    prepare_ticket_review,
    record_consent,
    request_case_update,
    request_ticket_submission,
    set_draft_status,
    update_ticket_draft,
    validate_draft,
)


def request(text="Current issue details", **fields):
    return ChatRequest(external_user_id="customer-1", text=text, preferred_language="en", **fields)


def refs_by_field(references, *, source="current_input"):
    return {item.field: item.id for item in references.public if item.source == source}


def complete_dialogue(*, review_required=False):
    now = datetime.utcnow()
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60, now=now))
    dialogue = make_prompt(dialogue, "consent", "consent-turn")
    consent = record_consent(
        dialogue,
        prompt_id=dialogue.pending_prompt.id,
        originating_turn="consent-turn",
        customer_input="Yes",
        language="en",
        now=now,
    )
    assert consent.status == "prepared"
    dialogue = consent.dialogue
    references = build_field_references(
        request(
            "My receipt is missing",
            name="Alex Tan",
            email="alex@example.com",
            phone_number="012-345 6789",
        ),
        "My receipt is missing",
        pending_prompt=make_prompt(dialogue, "field", "issue-turn", field="description").pending_prompt,
    )
    result = update_ticket_draft(
        dialogue,
        references,
        {
            field: reference
            for field, reference in refs_by_field(references).items()
            if field in {"name", "email", "phone_number", "description"}
        },
        now=now,
    )
    assert result.status == "prepared" and result.validation.valid
    result.dialogue.draft.review_required = review_required
    return result.dialogue


def test_dialogue_models_keep_draft_prompt_and_unassigned_evidence():
    dialogue = DialogueData(evidence_group="unassigned-media")
    dialogue.draft = new_draft(
        expiry_minutes=60,
        now=datetime(2026, 9, 30),
        evidence_group=dialogue.evidence_group,
    )
    dialogue = make_prompt(dialogue, "field", "turn-1", field="name")

    restored = DialogueData.model_validate_json(dialogue.model_dump_json())

    assert restored.schema_version == 1
    assert restored.draft.evidence_group == "unassigned-media"
    assert restored.pending_prompt.draft_id == restored.draft.id
    assert restored.pending_prompt.version == restored.draft.version


def test_field_references_expose_roles_but_not_private_values_and_reject_forgery():
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    references = build_field_references(
        request(name="Alex", email="alex@example.com"), "Current issue details"
    )

    assert "Alex" not in repr(references.public)
    forged = update_ticket_draft(dialogue, references, {"name": "made-up-reference"})

    assert forged.status == "rejected"
    assert forged.reason == "unknown or mismatched customer field reference"
    assert forged.dialogue.draft.fields.name is None


@pytest.mark.parametrize(
    ("field", "text", "value"),
    [
        ("email", "My email is corrected@example.com", "corrected@example.com"),
        ("name", "My name is Jamie", "Jamie"),
        ("phone_number", "Phone: +60198765432", "+60198765432"),
    ],
)
def test_current_correction_cannot_be_undone_with_an_old_draft_reference(field, text, value):
    dialogue = make_prompt(complete_dialogue(), "details", "details-turn")
    references = build_field_references(
        request(text), text, pending_prompt=dialogue.pending_prompt, draft=dialogue.draft
    )
    assert [item.source for item in references.public if item.field == field] == ["current_input"]
    current = update_ticket_draft(dialogue, references, refs_by_field(references))
    other_draft_fields = refs_by_field(references, source="draft")
    assert other_draft_fields

    replayed = update_ticket_draft(current.dialogue, references, other_draft_fields)

    assert current.status == replayed.status == "prepared"
    assert getattr(replayed.dialogue.draft.fields, field) == value


def test_reference_cannot_be_relabelled_to_invent_a_value():
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    references = build_field_references(request(name="Alex"), "Current issue details")
    name_reference = refs_by_field(references)["name"]

    result = update_ticket_draft(dialogue, references, {"email": name_reference})

    assert result.status == "rejected"
    assert result.dialogue.draft.fields.email is None


def test_ambiguous_email_candidates_are_not_available_to_tools():
    customer_request = request("Use a@example.com or b@example.com")
    extraction = extract_customer_fields(customer_request, customer_request.text)
    references = build_field_references(customer_request, customer_request.text)

    assert extraction.ambiguous_fields == ("email",)
    assert "email" not in refs_by_field(references)
    result = update_ticket_draft(
        DialogueData(draft=new_draft(expiry_minutes=60)), references, {}
    )
    assert result.status == "rejected" and result.reason == "ambiguous_customer_values"


@pytest.mark.parametrize(
    "text",
    (
        "If I changed my email to hypothetical@example.com, what would happen?",
        'My email is "quoted@example.com".',
        "Please do not change my email to negated@example.com.",
        "What should I do with question@example.com?",
        "Can I use can-i@example.com?",
        "Is this email question-en@example.com",
        "Jika saya tukar e-mel kepada malay@example.com, apa berlaku?",
        "Ini e-mel saya question-ms@example.com ke",
        "如果我把邮箱改成 chinese@example.com，会怎样？",
        "我的邮箱是 question-zh@example.com 吗",
        "'My email is quoted-clause@example.com for this example.'",
        "My email is fake-en@example.com, not my real email.",
        "E-mel saya fake-ms@example.com, bukan e-mel sebenar saya.",
        "我的邮箱是 fake-zh@example.com，不是我的真实邮箱。",
        "What would happen if I changed my phone to +60123456789?",
    ),
)
def test_hypothetical_or_negated_contact_values_are_not_customer_fields(text):
    extraction = extract_customer_fields(request(text), text)
    references = build_field_references(request(text), text)

    assert "email" not in extraction.values
    assert "phone_number" not in extraction.values
    assert "email" not in refs_by_field(references)
    assert "phone_number" not in refs_by_field(references)


@pytest.mark.parametrize(
    ("text", "email"),
    (
        ("My email is corrected@example.com. What happens after that?", "corrected@example.com"),
        ("Here's my email: apostrophe@example.com.", "apostrophe@example.com"),
        ("E-mel saya ialah betul@example.com. Apa seterusnya?", "betul@example.com"),
        ("我的邮箱是 zhengque@example.com。接下来呢？", "zhengque@example.com"),
    ),
)
def test_affirmative_contact_correction_can_include_a_separate_side_question(text, email):
    extraction = extract_customer_fields(request(text), text)

    assert extraction.values["email"] == email


@pytest.mark.parametrize(
    "text",
    (
        "If my name is Hypothetical Person, what happens?",
        "Jika nama saya Ali Contoh, apa berlaku?",
        "如果我叫假设姓名，会怎样？",
    ),
)
def test_hypothetical_named_values_are_not_customer_fields(text):
    assert "name" not in extract_customer_fields(request(text), text).values


@pytest.mark.parametrize("text", ("My name is Alex Tan", "Nama saya Ali", "我叫王小明"))
def test_affirmative_named_values_remain_customer_fields(text):
    assert "name" in extract_customer_fields(request(text), text).values


def test_prompted_question_is_not_a_bare_name():
    assert "name" not in extract_customer_fields(
        request("如何预订行程"), "如何预订行程", prompted_field="name"
    ).values


def test_prompted_name_allows_an_apostrophe_within_the_name():
    assert extract_customer_fields(
        request("O'Connor"), "O'Connor", prompted_field="name"
    ).values["name"] == "O'Connor"


@pytest.mark.parametrize(
    ("fields", "missing", "invalid"),
    [
        ({}, ("name", "email", "phone_number", "description"), ()),
        (
            {"name": "Alex", "email": "bad", "phone_number": "123", "description": "Issue"},
            (),
            ("email", "phone_number"),
        ),
    ],
)
def test_validation_reports_missing_and_invalid_fields(fields, missing, invalid):
    draft = new_draft(expiry_minutes=60)
    for field, value in fields.items():
        setattr(draft.fields, field, value)

    validation = validate_draft(draft)

    assert validation.valid is False
    assert validation.missing_fields == missing
    assert validation.invalid_fields == invalid


def test_draft_updates_are_working_copies_and_idempotent():
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    references = build_field_references(request(name="Alex"), "Current issue details")
    selection = {"name": refs_by_field(references)["name"]}

    first = update_ticket_draft(dialogue, references, selection)
    second_refs = build_field_references(request(name="Alex"), "Current issue details")
    second = update_ticket_draft(
        first.dialogue, second_refs, {"name": refs_by_field(second_refs)["name"]}
    )

    assert dialogue.draft.fields.name is None
    assert first.dialogue.draft.fields.name == "Alex"
    assert second.dialogue.draft.version == first.dialogue.draft.version


def test_details_bound_rejects_addition_without_losing_previous_details():
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    dialogue.draft.fields.ride_details = "x" * 1_990
    prompt = make_prompt(dialogue, "details", "details-turn").pending_prompt
    references = build_field_references(
        request("twenty more characters"),
        "twenty more characters",
        pending_prompt=prompt,
    )

    result = update_ticket_draft(
        dialogue,
        references,
        {"ride_details": refs_by_field(references)["ride_details"]},
    )

    assert result.status == "rejected" and result.reason == "details_too_long"
    assert result.dialogue.draft.fields.ride_details == "x" * 1_990


def test_question_only_turn_is_not_extracted_as_ride_details():
    dialogue = make_prompt(DialogueData(draft=new_draft(expiry_minutes=60)), "details", "details-turn")
    references = build_field_references(
        request("How do I book a car?"),
        "How do I book a car?",
        pending_prompt=dialogue.pending_prompt,
        draft=dialogue.draft,
    )

    assert "ride_details" not in refs_by_field(references)


@pytest.mark.parametrize(
    "text",
    (
        "Downtown pickup was at midnight",
        "Issue pickup was at midnight",
        "Area pickup was at midnight",
        "Dokumen perjalanan saya menunjukkan masa tengah malam",
        "Boleh jadi pemandu tersalah jalan.",
        "Jika hujan, pemandu memandu terlalu laju.",
        "如果路上堵车，司机绕路了。",
        "Could not find the driver.",
    ),
)
def test_asserted_detail_prefixes_are_not_question_prefixes(text):
    dialogue = make_prompt(DialogueData(draft=new_draft(expiry_minutes=60)), "details", "details-turn")
    references = build_field_references(
        request(text), text, pending_prompt=dialogue.pending_prompt, draft=dialogue.draft
    )

    detail = next(
        references.resolve(item.id, item.field)[1]
        for item in references.public
        if item.source == "current_input" and item.field == "ride_details"
    )
    assert detail == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The driver was rude. How do I report this?", "The driver was rude."),
        ("Pemandu itu kasar. Bagaimana saya laporkan?", "Pemandu itu kasar."),
        ("司机很粗鲁。我该如何投诉？", "司机很粗鲁。"),
    ],
)
def test_mixed_assertion_and_question_keeps_asserted_detail(text, expected):
    dialogue = make_prompt(DialogueData(draft=new_draft(expiry_minutes=60)), "details", "details-turn")
    references = build_field_references(
        request(text), text, pending_prompt=dialogue.pending_prompt, draft=dialogue.draft
    )

    detail = next(
        references.resolve(item.id, item.field)[1]
        for item in references.public
        if item.source == "current_input" and item.field == "ride_details"
    )
    assert detail == expected


def test_repeated_detail_update_does_not_append_the_same_fragment_twice():
    dialogue = make_prompt(DialogueData(draft=new_draft(expiry_minutes=60)), "details", "details-turn")
    dialogue.draft.fields.ride_details = "The ride was yesterday"
    references = build_field_references(
        request("The receipt is still missing today"),
        "The receipt is still missing today",
        pending_prompt=dialogue.pending_prompt,
        draft=dialogue.draft,
    )
    selection = {"ride_details": refs_by_field(references)["ride_details"]}

    first = update_ticket_draft(dialogue, references, selection)
    second = update_ticket_draft(first.dialogue, references, selection)

    assert first.dialogue.draft.fields.ride_details == (
        "The ride was yesterday\nThe receipt is still missing today"
    )
    assert second.dialogue.draft.fields.ride_details == first.dialogue.draft.fields.ride_details


def test_contact_correction_during_details_is_not_copied_into_ride_details():
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    dialogue.draft.fields.phone_number = "+60123456789"
    prompt = make_prompt(dialogue, "details", "details-turn").pending_prompt

    references = build_field_references(
        request("Phone: +60198765432", phone_number="+60198765432"),
        "Phone: +60198765432",
        pending_prompt=prompt,
        draft=dialogue.draft,
    )

    assert set(refs_by_field(references)) == {"phone_number"}


def test_consent_requires_matching_current_prompt_and_customer_reply():
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    dialogue = make_prompt(dialogue, "consent", "turn-1")

    forged = record_consent(
        dialogue,
        prompt_id="wrong",
        originating_turn="turn-1",
        customer_input="Yes",
        language="en",
    )
    invented = record_consent(
        dialogue,
        prompt_id=dialogue.pending_prompt.id,
        originating_turn="turn-1",
        customer_input="please continue",
        language="en",
    )

    assert forged.status == invented.status == "rejected"
    assert forged.dialogue.draft.consent is None
    assert invented.dialogue.draft.consent is None

    api = record_consent(
        dialogue,
        prompt_id=dialogue.pending_prompt.id,
        originating_turn="turn-1",
        customer_input="Supplied details",
        language="en",
        api_control=True,
    )
    assert api.status == "prepared" and api.dialogue.draft.consent


def test_offer_acceptance_starts_draft_but_requires_separate_consent():
    dialogue = DialogueData()
    customer_request = request("My receipt is still missing")
    references = build_field_references(
        customer_request, customer_request.text, include_case_update=True
    )
    offered = prepare_ticket_offer(
        dialogue,
        references,
        description_reference=refs_by_field(references)["description"],
        issue_type="complaint",
        originating_turn="offer-turn",
    )

    assert offered.dialogue.draft is None
    accepted = accept_ticket_offer(
        offered.dialogue,
        prompt_id=offered.dialogue.pending_prompt.id,
        originating_turn="offer-turn",
        consent_prompt_turn="acceptance-turn",
        customer_input="Yes",
        language="en",
        expiry_minutes=60,
    )

    assert accepted.status == "prepared"
    assert accepted.dialogue.draft.fields.description == customer_request.text
    assert accepted.dialogue.draft.consent is None
    assert accepted.dialogue.pending_prompt.purpose == "consent"


def test_question_only_capability_text_cannot_prepare_optional_offer():
    customer_request = request("这个机器人能签署合作协议吗？")
    references = build_field_references(
        customer_request, customer_request.text, include_case_update=True
    )

    offered = prepare_ticket_offer(
        DialogueData(),
        references,
        description_reference=refs_by_field(references)["description"],
        issue_type="unconfirmed_question",
        originating_turn="offer-turn",
    )

    assert offered.status == "rejected"
    assert offered.reason == "offer_requires_asserted_detail"
    assert offered.dialogue.draft is None
    assert offered.dialogue.pending_offer is None


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "I do not want a refund; where are fare details?"),
        ("en", "What information is needed about a promo question?"),
        ("en", "That answer was terrible. Please explain booking again."),
        ("en", "Can I speak to a human?"),
        ("ms", "Di mana saya boleh melihat maklumat bayaran?"),
        ("ms", "Bolehkah saya bercakap dengan manusia?"),
        ("ms", "Bolehkah saya berkongsi OTP dengan chatbot?"),
        ("ms", "Halaman bantuan menyebut polis. Apakah maksudnya?"),
        ("ms", "Berhenti dulu."),
        ("ms", "Jangan padam akaun saya. Saya cuma perlukan panduan log masuk."),
        ("zh", "怎么排查登录问题？"),
        ("zh", "这个机器人能签署合作协议吗？"),
        ("zh", "可以找人工吗？"),
        ("zh", "先停一下。"),
        ("zh", "不要删除我的账号，我只需要登录指导。"),
        ("zh", "我不是要投诉优惠券问题，只想了解规则。"),
        ("en", "I am not in danger; I am reading the safety guidance."),
        ("ms", "Saya tidak dalam bahaya; saya membaca panduan keselamatan."),
        ("zh", "我没有危险，只是在阅读安全指南。"),
    ],
)
def test_optional_offer_rejects_questions_and_explicit_controls(language, text):
    customer_request = request(text)
    references = build_field_references(
        customer_request, text, include_case_update=True, include_description=True
    )

    offered = prepare_ticket_offer(
        DialogueData(),
        references,
        description_reference=refs_by_field(references)["description"],
        issue_type="unconfirmed_question",
        originating_turn="offer-turn",
        language=language,
    )

    assert offered.status == "rejected"
    assert offered.dialogue.pending_offer is None
    assert offered.reason in {"offer_requires_asserted_detail", "offer_control_not_proven"}


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "My voucher did not work"),
        ("en", "I cannot log in"),
        ("ms", "Saya tidak boleh log masuk"),
        ("zh", "我无法登录"),
        ("zh", "我要投诉优惠券问题"),
        ("zh", "我需要人工帮助"),
        ("en", "I was injured in a crash"),
        ("ms", "Saya cedera dalam kemalangan"),
        ("zh", "我撞车受伤了"),
        ("en", "I am not in danger now, but my driver threatened me"),
        ("ms", "Saya tidak dalam bahaya sekarang, tetapi pemandu saya mengugut saya"),
        ("zh", "我现在没有危险，但司机威胁了我"),
        ("en", 'The app says "login failed"; I cannot log in'),
        ("en", "Please create a ticket because my booking failed."),
    ],
)
def test_optional_offer_accepts_actual_issues(language, text):
    customer_request = request(text)
    references = build_field_references(
        customer_request, text, include_case_update=True, include_description=True
    )

    offered = prepare_ticket_offer(
        DialogueData(),
        references,
        description_reference=refs_by_field(references)["description"],
        issue_type="unconfirmed_question",
        originating_turn="offer-turn",
        language=language,
    )

    assert offered.status == "prepared"
    assert offered.dialogue.pending_offer.description == text


@pytest.mark.parametrize(
    ("text", "blocked"),
    [
        ("I am not proposing a partnership; I want company information.", True),
        ("We're not seeking a partnership; where is the company information?", True),
        ("I am not requesting a partnership; just explain the rules.", True),
        ("I am not receiving receipts.", False),
        ("I am proposing a partnership.", False),
        ("I need a human to discuss a partnership.", False),
    ],
)
def test_negated_intent_controls_optional_offers_and_fresh_draft_entry(text, blocked):
    customer_request = request(text)
    references = build_field_references(
        customer_request, text, include_case_update=True, include_description=True
    )
    offered = prepare_ticket_offer(
        DialogueData(),
        references,
        description_reference=refs_by_field(references)["description"],
        issue_type="unconfirmed_question",
        originating_turn="offer-turn",
        language="en",
    )

    assert is_new_draft_control(text, "en") is blocked
    assert (offered.status == "rejected") is blocked
    assert (offered.dialogue.pending_offer is None) is blocked


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "Can I speak to a human?"),
        ("en", "Why was I charged twice?"),
        ("en", "I cannot log in"),
        ("ms", "Bolehkah saya bercakap dengan manusia?"),
        ("ms", "Saya tidak boleh log masuk"),
        ("zh", "可以找人工吗？"),
        ("zh", "我无法登录"),
        ("en", "I was injured in a crash"),
        ("ms", "Saya cedera dalam kemalangan"),
        ("zh", "我撞车受伤了"),
        ("en", "I am not in danger now, but my driver threatened me"),
        ("ms", "Saya tidak dalam bahaya sekarang, tetapi pemandu saya mengugut saya"),
        ("zh", "我现在没有危险，但司机威胁了我"),
    ],
)
def test_new_draft_control_helper_preserves_actual_questions_and_issues(language, text):
    assert not is_new_draft_control(text, language)


@pytest.mark.parametrize(
    "text",
    [
        "What are human support hours?",
        "Perkataan penipuan ada dalam amaran; adakah panduan?",
        "Can I see payment information?",
    ],
)
def test_unproven_questions_are_new_draft_controls(text):
    assert is_new_draft_control(text, "en" if text.startswith(("What", "Can")) else "ms")


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "I do not want a refund"),
        ("en", "No, I don't want a ticket"),
        ("en", "I do not want human support"),
        ("ms", "Saya tidak mahu pegawai"),
        ("zh", "我不需要人工"),
        ("en", "I do not want a refund; I need a human to help"),
        ("ms", "Saya tidak mahu bayaran balik; saya perlukan pegawai"),
        ("zh", "我不想退款，但我需要人工帮助"),
        ("en", "I decline"),
        ("en", "Do not delete my account. I just need login guidance."),
        ("ms", "Jangan padam akaun saya. Saya cuma perlukan panduan log masuk."),
        ("ms", "Berhenti dulu."),
        ("zh", "不要删除我的账号，我只需要登录指导。"),
        ("zh", "我不是要投诉优惠券问题，只想了解规则。"),
        ("zh", "先停一下。"),
        ("en", "I am not in danger; I am reading the safety guidance."),
        ("ms", "Saya tidak dalam bahaya; saya membaca panduan keselamatan."),
        ("zh", "我没有危险，只是在阅读安全指南。"),
    ],
)
def test_new_draft_control_helper_recognizes_anchored_controls(language, text):
    assert is_new_draft_control(text, language)


def test_question_only_issue_remains_recordable_in_existing_draft():
    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)),
        "field",
        "description-turn",
        field="description",
    )
    text = "Why was I charged twice?"
    references = build_field_references(
        request(text), text, pending_prompt=dialogue.pending_prompt, draft=dialogue.draft
    )
    result = update_ticket_draft(
        dialogue, references, {"description": refs_by_field(references)["description"]}
    )

    assert result.status == "prepared"
    assert result.dialogue.draft.fields.description == text


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "What are human support hours?"),
        ("ms", "Perkataan penipuan ada dalam amaran; adakah panduan?"),
    ],
)
def test_unproven_current_question_does_not_mutate_existing_draft_or_prompt(
    language, text
):
    dialogue = DialogueData(draft=new_draft(expiry_minutes=60))
    dialogue.draft.fields.description = "Existing issue"
    dialogue = make_prompt(dialogue, "field", "description-turn", field="description")
    request_in_language = ChatRequest(
        external_user_id="customer-1",
        text=text,
        preferred_language=language,
    )
    references = build_field_references(
        request_in_language,
        text,
        pending_prompt=dialogue.pending_prompt,
        draft=dialogue.draft,
        include_description=True,
    )
    before = dialogue.model_dump(mode="json")
    result = update_ticket_draft(
        dialogue,
        references,
        {"description": refs_by_field(references)["description"]},
    )

    assert result.status == "rejected"
    assert result.reason == "description_question_not_proven"
    assert result.dialogue.model_dump(mode="json") == before


def test_unproven_question_cannot_start_an_empty_draft():
    text = "What are human support hours?"
    customer_request = request(text)
    references = build_field_references(
        customer_request, text, include_description=True
    )
    result = update_ticket_draft(
        DialogueData(),
        references,
        {"description": refs_by_field(references)["description"]},
    )

    assert result.status == "rejected"
    assert result.reason == "description_question_not_proven"
    assert result.dialogue.draft is None


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "Why was I charged twice?"),
        ("en", "Can I speak to a human?"),
        ("ms", "Bolehkah saya bercakap dengan manusia?"),
        ("zh", "可以找人工吗？"),
        ("en", "My voucher did not work"),
        ("ms", "Saya tidak boleh log masuk"),
        ("zh", "我无法登录我的账号"),
    ],
)
def test_assessed_or_asserted_description_is_stored_verbatim(language, text):
    customer_request = ChatRequest(
        external_user_id="customer-1",
        text=text,
        preferred_language=language,
    )
    references = build_field_references(
        customer_request, text, include_description=True
    )
    result = update_ticket_draft(
        DialogueData(),
        references,
        {"description": refs_by_field(references)["description"]},
    )

    assert result.status == "prepared"
    assert result.dialogue.draft.fields.description == text


def test_current_offer_and_consent_declines_clear_only_the_matching_control():
    customer_request = request("My receipt is still missing")
    references = build_field_references(
        customer_request, customer_request.text, include_case_update=True
    )
    offered = prepare_ticket_offer(
        DialogueData(),
        references,
        description_reference=refs_by_field(references)["description"],
        issue_type="complaint",
        originating_turn="offer-turn",
    )
    declined_offer = accept_ticket_offer(
        offered.dialogue,
        prompt_id=offered.dialogue.pending_prompt.id,
        originating_turn="offer-turn",
        consent_prompt_turn="unused",
        customer_input="No",
        language="en",
        expiry_minutes=60,
    )
    assert declined_offer.status == "prepared"
    assert declined_offer.dialogue.pending_offer is None
    assert declined_offer.dialogue.draft is None

    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)), "consent", "consent-turn"
    )
    declined_consent = record_consent(
        dialogue,
        prompt_id=dialogue.pending_prompt.id,
        originating_turn="consent-turn",
        customer_input="No",
        language="en",
    )
    assert declined_consent.status == "prepared"
    assert declined_consent.dialogue.pending_prompt is None
    assert declined_consent.dialogue.draft.status == "cancelled"


def test_review_confirmation_is_invalidated_by_correction():
    dialogue = complete_dialogue(review_required=True)
    review = prepare_ticket_review(dialogue, originating_turn="review-turn")
    assert review.status == "prepared"
    stale_prompt = review.dialogue.pending_prompt
    references = build_field_references(
        request(email="corrected@example.com"), "Current issue details"
    )
    corrected = update_ticket_draft(
        review.dialogue,
        references,
        {"email": refs_by_field(references)["email"]},
    )
    submission = request_ticket_submission(
        corrected.dialogue,
        prompt_id=stale_prompt.id,
        originating_turn="review-turn",
        customer_input="Submit",
        language="en",
    )

    assert corrected.dialogue.pending_prompt is None
    assert submission.status == "rejected"
    assert submission.reason == "stale_or_mismatched_submission_prompt"


def test_done_skip_and_no_complete_details_but_withdrawal_does_not():
    dialogue = complete_dialogue()
    dialogue = make_prompt(dialogue, "details", "details-turn")
    for reply in ("Done", "Skip", "No"):
        result = request_ticket_submission(
            dialogue,
            prompt_id=dialogue.pending_prompt.id,
            originating_turn="details-turn",
            customer_input=reply,
            language="en",
        )
        assert result.status == "prepared" and result.operation

    withdrawn = request_ticket_submission(
        dialogue,
        prompt_id=dialogue.pending_prompt.id,
        originating_turn="details-turn",
        customer_input="I decline",
        language="en",
    )
    assert withdrawn.status == "rejected" and withdrawn.reason == "consent_withdrawn"


def test_review_requires_submit_and_submission_staging_is_idempotent():
    dialogue = complete_dialogue(review_required=True)
    dialogue = prepare_ticket_review(dialogue, originating_turn="review-turn").dialogue
    kwargs = dict(
        prompt_id=dialogue.pending_prompt.id,
        originating_turn="review-turn",
        customer_input="Submit",
        language="en",
    )

    first = request_ticket_submission(dialogue, **kwargs)
    second = request_ticket_submission(dialogue, **kwargs)
    yes = request_ticket_submission(dialogue, **{**kwargs, "customer_input": "Yes"})

    assert first.status == second.status == "prepared"
    assert first.operation.operation_id == second.operation.operation_id
    assert yes.status == "rejected" and yes.reason == "submission_not_confirmed"


def test_status_changes_require_explicit_customer_intent():
    dialogue = complete_dialogue()

    invented = set_draft_status(
        dialogue, "paused", customer_input="Alex", language="en"
    )
    paused = set_draft_status(
        dialogue, "paused", customer_input="Pause this for later", language="en"
    )
    references = build_field_references(request(name="Changed"), "Current issue details")
    paused_update = update_ticket_draft(
        paused.dialogue,
        references,
        {"name": refs_by_field(references)["name"]},
    )
    resumed = set_draft_status(
        paused.dialogue, "active", customer_input="Continue", language="en"
    )

    assert invented.status == "rejected"
    assert paused.dialogue.draft.status == "paused"
    assert paused_update.status == "rejected" and paused_update.reason == "draft_paused"
    assert resumed.dialogue.draft.status == "active"


@pytest.mark.parametrize(
    ("status", "text"),
    (
        ("paused", "What happens if I pause and continue later?"),
        ("paused", 'The word "pause" is in my question.'),
        ("paused", "Please do not pause this draft."),
        ("paused", "Jika saya jeda draf ini, apa akan berlaku?"),
        ("paused", "如果我暂停草稿会怎样？"),
        ("active", "What would happen if I resume?"),
        ("cancelled", "What happens if I cancel ticket?"),
    ),
)
def test_hypothetical_or_negated_status_controls_do_not_change_a_draft(status, text):
    dialogue = make_prompt(complete_dialogue(), "details", "control-turn")
    if status == "active":
        dialogue = set_draft_status(
            dialogue, "paused", customer_input="Pause this for later", language="en"
        ).dialogue
    prompt_id = dialogue.pending_prompt.id if dialogue.pending_prompt else None

    result = set_draft_status(dialogue, status, customer_input=text, language="en")

    assert result.status == "rejected"
    assert result.dialogue.draft.status == dialogue.draft.status
    assert (result.dialogue.pending_prompt.id if result.dialogue.pending_prompt else None) == prompt_id


def test_owned_case_view_rejects_another_customer():
    common = dict(
        case_id="case-1",
        public_id="DUDU-20260930-ABCDE",
        status="closed",
        channel="web",
        external_user_id="owner",
        expected_channel="web",
        update_count=0,
    )

    assert owned_case_view(**common, expected_external_user_id="other") is None
    owned = owned_case_view(**common, expected_external_user_id="owner")
    assert owned and owned.model_dump() == {
        "id": "case-1",
        "public_id": "DUDU-20260930-ABCDE",
        "status": "closed",
        "version": 1,
        "update_count": 0,
    }


def test_greeting_cannot_become_a_case_update_reference():
    customer_request = request("Hello")
    references = build_field_references(
        customer_request, customer_request.text, include_case_update=True
    )

    assert "description" not in refs_by_field(references)


def test_case_update_requires_owned_versioned_confirmation_and_is_idempotent():
    case = owned_case_view(
        case_id="case-1",
        public_id="DUDU-20260930-ABCDE",
        status="closed",
        version=3,
        channel="web",
        external_user_id="owner",
        expected_channel="web",
        expected_external_user_id="owner",
        update_count=0,
    )
    dialogue = DialogueData(last_case_reference=case.public_id)
    references = build_field_references(
        request("Please add that my receipt is still missing"),
        "Please add that my receipt is still missing",
        include_case_update=True,
    )
    update_reference = refs_by_field(references)["description"]
    prepared = request_case_update(
        dialogue,
        case,
        references,
        update_reference=update_reference,
        originating_turn="case-turn",
    )
    different_references = build_field_references(
        request("A different update"),
        "A different update",
        include_case_update=True,
    )
    conflicting = request_case_update(
        prepared.dialogue,
        case,
        different_references,
        update_reference=refs_by_field(different_references)["description"],
        originating_turn="case-turn",
    )
    stale_case = case.model_copy(update={"version": 4})
    stale = request_case_update(
        prepared.dialogue,
        stale_case,
        references,
        update_reference=update_reference,
        originating_turn="case-turn",
        customer_input="Yes",
        prompt_id=prepared.dialogue.pending_prompt.id,
    )
    restored = DialogueData.model_validate_json(prepared.dialogue.model_dump_json())
    confirmation = dict(
        originating_turn="case-turn",
        customer_input="Yes",
        prompt_id=prepared.dialogue.pending_prompt.id,
    )
    first = request_case_update(restored, case, references, **confirmation)
    second = request_case_update(restored, case, references, **confirmation)

    assert stale.status == "rejected"
    assert conflicting.status == "rejected" and conflicting.reason == "conflicting_case_update"
    assert restored.pending_case_update.update == "Please add that my receipt is still missing"
    assert first.operation.confirmed
    assert first.operation.reopen
    assert first.operation.operation_id == second.operation.operation_id


def test_local_control_phrases_distinguish_completion_from_withdrawal():
    assert consent_answer("No", "en") is False
    assert is_done("Done", "en")
    assert is_skip("Skip", "en")
    assert not is_explicit_withdrawal("No")
    assert is_explicit_withdrawal("I decline")
