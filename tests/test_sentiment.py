"""Tests for api/sentiment.py."""

from unittest.mock import MagicMock, patch

from api.sentiment import SentimentAnalyzer


def test_classify_comments_records_cost_when_usage_present(tmp_path):
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = (
        '{"results":[{"sentiment":"negative","confidence":0.9,'
        '"normalized_topic":"zoning","summary":"Opposed the proposal."}]}'
    )
    response.usage = MagicMock(prompt_tokens=95, completion_tokens=21)
    client.chat.completions.create.return_value = response
    tracker = MagicMock()
    analyzer = SentimentAnalyzer(str(tmp_path / "sentiment.db"), openai_client=client)

    with patch("api.cost.get_cost_tracker", return_value=tracker):
        results = analyzer.classify_comments(
            [{"speaker": "Jane", "topic": "Housing", "summary": "Opposed the change."}],
            tenant_id="tenant-a",
            request_id="req-1",
        )

    assert results[0]["normalized_topic"] == "zoning"
    tracker.record_llm_call.assert_called_once_with(
        tenant_id="tenant-a",
        model="gpt-4o-mini",
        operation="public_comment_sentiment",
        input_tokens=95,
        output_tokens=21,
        request_id="req-1",
        module="sentiment",
    )
