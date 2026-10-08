from app.repo import load_user


def nightly(user_ids):
    return [load_user(i) for i in user_ids]
