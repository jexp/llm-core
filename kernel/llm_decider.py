"""Lazy pydantic-ai adapter for LLM-backed decisions and interpretation.

Any import/API drift or transport failure raises LLMUnavailable; callers degrade to their
declared fallbacks. The kernel never crashes because a provider changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from kernel.config import Settings
    from kernel.decide import DecisionDefinition


class LLMUnavailable(Exception):
    pass


def _build_agent(settings: Settings, result_type: type[BaseModel], system_prompt: str,
                 model_name: str | None = None):
    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as e:
        raise LLMUnavailable(f"pydantic-ai not installed: {e}") from e
    if not settings.llm_enabled:
        raise LLMUnavailable("LLM backend not configured")
    model = OpenAIModel(
        model_name or settings.llmc_model,
        provider=OpenAIProvider(base_url=settings.llmc_base_url, api_key=settings.llmc_api_key),
    )
    return Agent(model=model, result_type=result_type, system_prompt=system_prompt)


def run_typed(settings: Settings, system_prompt: str, prompt: str, result_type: type[BaseModel],
              model_name: str | None = None):
    try:
        agent = _build_agent(settings, result_type, system_prompt, model_name)
        result = agent.run_sync(prompt)
        output = getattr(result, "output", None)
        return output if output is not None else result.data
    except LLMUnavailable:
        raise
    except Exception as e:
        raise LLMUnavailable(f"LLM call failed: {e}") from e


class _RawAnswer(BaseModel):
    type: str
    value: Any
    probabilities: dict[str, float] = {}
    confidence: float | None = None


class _BatteryResult(BaseModel):
    answers: dict[str, _RawAnswer]


def run_decision_battery_llm(settings: Settings, defn: DecisionDefinition, state: dict,
                             model_name: str | None = None) -> dict:
    """LLM fallback for a decision battery: same typed answers, one call."""
    import json

    from kernel.decide import Answer

    battery = {
        name: {"type": q["type"], "instructions": q["instructions"],
               "criteria": q.get("criteria"), "options": (list(q["criteria"]) if isinstance(q.get("criteria"), dict) else q.get("criteria"))}
        for name, q in defn.questions.items()
    }
    system = (
        "You are a decision-model fallback. Given a state and a battery of typed questions "
        "(choice / score / noul), answer every question with the exact typed shape. "
        "For choice, value must be one of options. For score, value is a number 1..len(levels). "
        "For noul, value is a probability 0..1. Provide calibrated confidence in 0..1."
    )
    prompt = f"STATE:\n{json.dumps(state, default=str)[:6000]}\n\nQUESTIONS:\n{json.dumps(battery)}"
    result = run_typed(settings, system, prompt, _BatteryResult, model_name=model_name)
    answers: dict[str, Answer] = {}
    for name, raw in result.answers.items():
        if name not in defn.questions:
            continue
        answers[name] = Answer(
            type=defn.questions[name]["type"],
            value=raw.value,
            probabilities=raw.probabilities,
            confidence=raw.confidence,
        )
    missing = set(defn.questions) - set(answers)
    if missing:
        raise LLMUnavailable(f"LLM battery missed questions: {sorted(missing)}")
    return answers
