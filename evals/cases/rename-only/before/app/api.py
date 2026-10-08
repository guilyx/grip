from app.repo import fetch_user


def me(request):
    return fetch_user(request.user_id)
