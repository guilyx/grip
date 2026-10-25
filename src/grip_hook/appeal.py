"""``grip appeal``: one question of the last graded quiz, graded again.

A grade can be wrong: the model misread the code, or the rubric missed a correct answer.
The developer says why their answer was right; the same provider grades the original
answer again with that justification next to it, as data. The justification earns nothing
on its own, an appeal never lowers a score, and each question of a quiz can be appealed
once, so appealing is not a way to retry the quiz.
"""

from __future__ import annotations

from grip_hook import MAX_SCORE
from grip_hook.errors import GripError
from grip_hook.git import Diff
from grip_hook.models import Answer, Appeal, Difficulty, Report
from grip_hook.providers.base import Provider

MAX_JUSTIFICATION = 4000
"""Characters, the same limit as one whiteboard answer."""


def appeal(
    report: Report,
    index: int,
    justification: str,
    *,
    diff: Diff,
    provider: Provider,
    difficulty: Difficulty,
) -> Report:
    """``report`` with question ``index`` graded again, and the appeal recorded.

    Raises :class:`GripError` when the question does not exist, was appealed already, or
    the justification is empty or too long.
    """
    justification = justification.strip()
    if not 0 <= index < len(report.questions):
        raise GripError(f"there is no question {index + 1}; the quiz had {len(report.questions)}")
    if any(a.question_index == index for a in report.appeals):
        raise GripError(f"question {index + 1} of this quiz was appealed already")
    if not justification:
        raise GripError('say why your answer was right: grip appeal N "because ..."')
    if len(justification) > MAX_JUSTIFICATION:
        raise GripError(f"keep the justification under {MAX_JUSTIFICATION} characters")
    grades = {g.question_index: g for g in report.grades}
    if index not in grades:
        raise GripError(f"question {index + 1} was not graded")
    old = grades[index]
    answer = next(
        (a for a in report.answers if a.question_index == index),
        Answer(question_index=index, text=""),
    )
    regraded = provider.appeal(
        diff,
        report.questions[index],
        answer,
        old,
        justification=justification,
        difficulty=difficulty,
    )
    # The score is in range (``QuestionGrade`` checks it); its question_index is ignored.
    new_score = max(old.score, regraded.score)
    record = Appeal(
        question_index=index,
        justification=justification,
        old_score=old.score,
        new_score=new_score,
        feedback=regraded.feedback,
    )
    if new_score > old.score:
        grades[index] = old.model_copy(update={"score": new_score, "feedback": regraded.feedback})
    ordered = [grades[i] for i in sorted(grades)]
    score = min(sum(g.score for g in ordered), MAX_SCORE)
    return report.model_copy(
        update={
            "grades": ordered,
            "score": score,
            "passed": score >= report.passing_score,
            "appeals": [*report.appeals, record],
        }
    )


__all__ = ["MAX_JUSTIFICATION", "appeal"]
