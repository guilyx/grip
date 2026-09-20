"""grip: keep a grip on your code.

A git hook that quizzes you about your own diff before you commit or push it.
"""

__version__ = "0.1.0"

QUESTION_COUNT = 5
"""Number of questions asked in every quiz. Fixed by design."""

POINTS_PER_QUESTION = 20
"""Maximum points per question. ``QUESTION_COUNT * POINTS_PER_QUESTION == 100``."""

MAX_SCORE = QUESTION_COUNT * POINTS_PER_QUESTION

__all__ = ["MAX_SCORE", "POINTS_PER_QUESTION", "QUESTION_COUNT", "__version__"]
