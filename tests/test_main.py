"""Focused tests for legacy summary generation in main.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from main import MeetingPipeline


def _pipeline_stub():
    pipeline = MeetingPipeline.__new__(MeetingPipeline)
    pipeline.force_reprocess = False
    pipeline.summary_model = "gpt-4o"
    pipeline.client = MagicMock()
    pipeline.progress = lambda *args, **kwargs: None
    pipeline.log = lambda *args, **kwargs: None
    pipeline.tenant_id = "tenant-a"
    return pipeline


def test_generate_summary_records_cost_when_usage_present(tmp_path):
    pipeline = _pipeline_stub()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "A" * 160
    response.usage = MagicMock(prompt_tokens=420, completion_tokens=96)
    pipeline.client.chat.completions.create.return_value = response
    tracker = MagicMock()
    summary_path = Path(tmp_path) / "summary.txt"

    with patch("api.cost.get_cost_tracker", return_value=tracker):
        summary = pipeline.generate_summary(
            clip_id=123,
            transcript="word " * 50,
            agenda_text=None,
            minutes_text=None,
            summary_txt_path=summary_path,
        )

    assert summary == "A" * 160
    tracker.record_llm_call.assert_called_once_with(
        tenant_id="tenant-a",
        model="gpt-4o",
        operation="legacy_summary_generation",
        input_tokens=420,
        output_tokens=96,
        request_id="legacy-summary:123",
        module="pipeline",
    )
