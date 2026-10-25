"""Adapter for Jev-compatible decision models (Typesafe AI System One).

Isolated so that SDK/API drift degrades to a typed `JevUnavailable` instead of breaking the
kernel — the decision service then follows its declared fallback chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kernel.config import Settings
    from kernel.decide import Answer, DecisionDefinition


class JevUnavailable(Exception):
    pass


def build_questions(defn: DecisionDefinition) -> dict:
    from typesafe_sdk import Choice, Noul, Score

    out = {}
    for name, q in defn.questions.items():
        if q["type"] == "choice":
            out[name] = Choice(instructions=q["instructions"], criteria=q["criteria"])
        elif q["type"] == "score":
            out[name] = Score(instructions=q["instructions"], criteria=q["criteria"])
        elif q["type"] == "noul":
            kwargs: dict[str, Any] = {"instructions": q["instructions"]}
            if q.get("criteria"):
                kwargs["criteria"] = q["criteria"]
            out[name] = Noul(**kwargs)
        else:
            raise JevUnavailable(f"unknown question type {q['type']!r}")
    return out


def run_battery(defn: DecisionDefinition, state: dict, settings: Settings) -> dict[str, Answer]:
    """Evaluate the definition's question battery against state. Raises JevUnavailable on any
    configuration, import, or transport failure."""
    from kernel.decide import Answer

    try:
        from typesafe_sdk import TypeSafeClient
    except ImportError as e:
        raise JevUnavailable(f"typesafe-sdk not installed: {e}") from e
    if not settings.jev_enabled:
        raise JevUnavailable("TYPESAFE_API_KEY not set")

    questions = build_questions(defn)
    try:
        with TypeSafeClient() as client:
            try:
                response = client.system_one(
                    model=settings.typesafe_jev_model, state=state, questions=questions
                )
            except TypeError:  # older SDKs may not accept model=
                response = client.system_one(state=state, questions=questions)
    except JevUnavailable:
        raise
    except Exception as e:
        raise JevUnavailable(f"system_one call failed: {e}") from e

    answers: dict[str, Answer] = {}
    for name, q in defn.questions.items():
        if q["type"] == "choice":
            a = response.choices[name]
            probs = {k: float(v) for k, v in (getattr(a, "probabilities", None) or {}).items()}
            answers[name] = Answer(
                type="choice",
                value=str(a.choice),
                probabilities=probs,
                confidence=float(a.confidence) if getattr(a, "confidence", None) is not None else None,
            )
        elif q["type"] == "score":
            a = response.scores[name]
            probs = {k: float(v) for k, v in (getattr(a, "probabilities", None) or {}).items()}
            answers[name] = Answer(
                type="score",
                value=float(a.score),
                probabilities=probs,
                confidence=float(a.confidence) if getattr(a, "confidence", None) is not None else None,
            )
        else:
            a = response.nouls[name]
            answers[name] = Answer(type="noul", value=float(a.noul))
    return answers
