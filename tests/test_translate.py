"""Tests for api/translate.py."""

from unittest.mock import MagicMock, call, patch

from api.translate import TranslationService


def test_translate_records_cost_when_usage_present():
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "Hola mundo"
    response.usage = MagicMock(prompt_tokens=120, completion_tokens=30)
    client.chat.completions.create.return_value = response
    tracker = MagicMock()

    with patch("api.cost.get_cost_tracker", return_value=tracker):
        service = TranslationService(client)
        translated = service.translate(
            "Hello world",
            "es",
            tenant_id="tenant-a",
            request_id="req-1",
        )

    assert translated == "Hola mundo"
    tracker.record_llm_call.assert_called_once_with(
        tenant_id="tenant-a",
        model="gpt-4o",
        operation="translate_response",
        input_tokens=120,
        output_tokens=30,
        request_id="req-1",
        module="translate",
    )


def test_translate_response_threads_tracking_context():
    service = TranslationService(MagicMock())
    result = {
        "answer": "Answer",
        "content": "Content",
        "sources": [{"excerpt": "Excerpt"}],
    }

    with patch.object(service, "translate", side_effect=["Uno", "Dos", "Tres"]) as translate_mock:
        translated = service.translate_response(
            result,
            "es",
            tenant_id="tenant-a",
            request_id="req-2",
        )

    assert translated["answer"] == "Uno"
    assert translated["content"] == "Dos"
    assert translated["sources"][0]["excerpt"] == "Tres"
    assert translated["language"] == "es"
    assert translate_mock.call_args_list == [
        call("Answer", "es", "gpt-4o", tenant_id="tenant-a", request_id="req-2"),
        call("Content", "es", "gpt-4o", tenant_id="tenant-a", request_id="req-2"),
        call("Excerpt", "es", "gpt-4o", tenant_id="tenant-a", request_id="req-2"),
    ]
