from app.repo import load_user


def me(request):
    return load_user(request.user_id)
