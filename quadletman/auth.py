import grp
import logging
import pwd

from fastapi import Cookie, Request

from . import session as session_store
from .config import settings

logger = logging.getLogger(__name__)


class NotAuthenticated(Exception):
    pass


def _user_in_allowed_group(username: str) -> bool:
    try:
        user_groups = {g.gr_name for g in grp.getgrall() if username in g.gr_mem}
        # also include primary group
        pw = pwd.getpwnam(username)
        primary_group = grp.getgrgid(pw.pw_gid).gr_name
        user_groups.add(primary_group)
        return bool(user_groups & set(settings.allowed_groups))
    except KeyError:
        return False


def require_auth(request: Request, qm_session: str = Cookie(default=None)) -> str:
    if settings.test_auth_user:
        logger.critical(
            "SECURITY: test auth bypass active — request %s %s authenticated as %r without PAM",
            request.method,
            request.url.path,
            settings.test_auth_user,
        )
        return settings.test_auth_user
    if qm_session:
        user = session_store.get_session(qm_session)
        if user:
            return user
    raise NotAuthenticated()
