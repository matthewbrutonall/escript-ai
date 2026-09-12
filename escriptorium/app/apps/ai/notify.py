"""Channel-layer notifications cannot serialize gettext_lazy proxies."""


def notify_user(user, message, **kwargs):
    if not user:
        return
    user.notify(str(message), **kwargs)
