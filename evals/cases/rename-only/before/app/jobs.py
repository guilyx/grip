from app.repo import fetch_user


def nightly(user_ids):
    return [fetch_user(i) for i in user_ids]
