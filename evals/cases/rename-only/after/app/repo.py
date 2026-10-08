import warnings

from app.db import session


def load_user(user_id: int):
    return session.get("users", user_id)


def fetch_user(user_id: int):
    warnings.warn("fetch_user is now load_user", DeprecationWarning, stacklevel=2)
    return load_user(user_id)
