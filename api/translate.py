"""Translation service for RAG responses.

Uses GPT-4o to translate answers into the user's language while
preserving:
  - Citation format [Clip ID, MM:SS]
  - Markdown formatting (**bold**, *italic*, lists)
  - Proper nouns (names, places, organization names)
  - Dollar amounts and numbers
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _coerce_token_count(value) -> int | None:
    """Convert a usage field to int only when it is actually numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _usage_value(usage, *names: str) -> int:
    """Read the first available integer token field from a usage object."""
    if usage is None:
        return 0
    for name in names:
        value = _coerce_token_count(getattr(usage, name, None))
        if value is not None:
            return value
    return 0


def _record_llm_cost(
    tenant_id: str | None,
    usage,
    *,
    model: str,
    operation: str,
    request_id: str | None = None,
) -> None:
    """Best-effort completion cost tracking; never raises."""
    if not tenant_id or usage is None:
        return

    input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    if input_tokens <= 0 and output_tokens <= 0:
        return

    try:
        from api.cost import get_cost_tracker

        get_cost_tracker().record_llm_call(
            tenant_id=tenant_id,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
            module="translate",
        )
    except Exception:
        logger.debug("cost tracking failed for translation call", exc_info=True)

# Languages supported for translation.
# Keys match the frontend locale codes.
SUPPORTED_LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "zh": "Chinese (Simplified)",
    "vi": "Vietnamese",
    "ko": "Korean",
}

TRANSLATION_SYSTEM_PROMPT = """You are a professional translator for a government meeting archive system.

Translate the following text from English into {target_language}.

Rules:
1. Preserve all citation references exactly as-is. Citations look like [Clip 1234, 5:30] -- do NOT translate or modify them.
2. Preserve all Markdown formatting (**, *, ##, bullet lists, numbered lists).
3. Keep proper nouns (person names, place names, organization names) in their original English form.
4. Keep dollar amounts and numbers in their original format (e.g. $18,040,000).
5. Translate government terminology accurately:
   - ordinance, resolution, motion, amendment, proclamation
   - agenda, minutes, public hearing, consent agenda
   - appropriation, bond, grant, contract
6. Maintain the same paragraph structure and line breaks.
7. Return ONLY the translated text, no explanations or notes.
"""


class TranslationService:
    """Translates RAG responses to the user's requested language."""

    def __init__(self, openai_client):
        self.client = openai_client

    def translate(
        self,
        text: str,
        target_lang: str,
        model: str = "gpt-4o",
        tenant_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> str:
        """Translate text to the target language.

        Args:
            text: The English text to translate.
            target_lang: Target language code (e.g. 'es', 'zh', 'vi', 'ko').
            model: OpenAI model to use for translation.

        Returns:
            Translated text, or original text if target is English or
            the language is not supported.
        """
        if not text or not text.strip():
            return text

        # No translation needed for English
        if target_lang == "en" or target_lang not in SUPPORTED_LANGUAGES:
            return text

        target_language = SUPPORTED_LANGUAGES[target_lang]

        try:
            response = self.client.chat.completions.create(
                model=model,
                temperature=0.2,
                messages=[
                    {
                        "role": "system",
                        "content": TRANSLATION_SYSTEM_PROMPT.format(
                            target_language=target_language
                        ),
                    },
                    {
                        "role": "user",
                        "content": text,
                    },
                ],
            )
            _record_llm_cost(
                tenant_id,
                getattr(response, "usage", None),
                model=model,
                operation="translate_response",
                request_id=request_id,
            )
            translated = response.choices[0].message.content
            return translated.strip() if translated else text
        except Exception:
            logger.exception(
                "Translation failed for lang=%s, returning original text",
                target_lang,
            )
            return text

    def translate_response(
        self,
        result: dict,
        target_lang: str,
        model: str = "gpt-4o",
        tenant_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> dict:
        """Translate a full RAG response dict (answer + source excerpts).

        Modifies the dict in place and returns it.

        Args:
            result: The RAG response dict with 'answer' and optional 'sources'.
            target_lang: Target language code.
            model: OpenAI model to use.

        Returns:
            The same dict with translated 'answer' and source excerpts.
        """
        if target_lang == "en" or target_lang not in SUPPORTED_LANGUAGES:
            return result

        # Translate the main answer
        if "answer" in result and result["answer"]:
            result["answer"] = self.translate(
                result["answer"], target_lang, model,
                tenant_id=tenant_id, request_id=request_id,
            )

        # Translate the main content (for chat responses)
        if "content" in result and result["content"]:
            result["content"] = self.translate(
                result["content"], target_lang, model,
                tenant_id=tenant_id, request_id=request_id,
            )

        # Translate source excerpts
        for source in result.get("sources", []):
            if "excerpt" in source and source["excerpt"]:
                source["excerpt"] = self.translate(
                    source["excerpt"], target_lang, model,
                    tenant_id=tenant_id, request_id=request_id,
                )

        # Tag the response with the language it was translated to
        result["language"] = target_lang

        return result
