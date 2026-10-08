from app.db import session


def fetch_user(user_id: int):
    return session.get("users", user_id)
