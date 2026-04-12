"""Focused tests for export FOIA cost attribution."""

from unittest.mock import MagicMock, patch

from api.export import ExportManager, ExportStore


def test_execute_foia_records_embedding_and_synthesis_costs(tmp_path):
    store = ExportStore(str(tmp_path / "exports.db"))
    manager = ExportManager(str(tmp_path), store)
    foia_req = store.create_foia_request("tenant-a", "budget")

    collection = MagicMock()
    collection.count.return_value = 1
    collection.query.return_value = {
        "ids": [["chunk-1"]],
        "documents": [["Budget funding was approved."]],
        "metadatas": [[{
            "clip_id": "6669",
            "date": "2026-01-08",
            "meeting_body": "Council",
            "source": "transcript",
            "start_time": 120,
        }]],
        "distances": [[0.01]],
    }

    openai_client = MagicMock()
    embed_response = MagicMock()
    embed_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
    embed_response.usage = MagicMock(total_tokens=14)
    openai_client.embeddings.create.return_value = embed_response

    chat_response = MagicMock()
    chat_response.choices = [MagicMock()]
    chat_response.choices[0].message.content = "FOIA summary"
    chat_response.usage = MagicMock(prompt_tokens=210, completion_tokens=44)
    openai_client.chat.completions.create.return_value = chat_response

    tracker = MagicMock()
    with patch("api.cost.get_cost_tracker", return_value=tracker):
        manager._execute_foia(
            foia_req,
            collection,
            openai_client,
            {"6669": {"title": "Council Meeting"}},
        )

    refreshed = store.get_foia_request(foia_req.request_id, "tenant-a")
    assert refreshed is not None
    assert refreshed.status == "completed"
    tracker.record_embedding.assert_called_once_with(
        tenant_id="tenant-a",
        model="text-embedding-3-small",
        tokens=14,
        request_id=foia_req.request_id,
        operation="foia_query_embed",
    )
    tracker.record_llm_call.assert_called_once_with(
        tenant_id="tenant-a",
        model="gpt-4o",
        operation="foia_synthesis",
        input_tokens=210,
        output_tokens=44,
        request_id=foia_req.request_id,
        module="export",
    )

    manager._executor.shutdown(wait=False)
    store.close()
