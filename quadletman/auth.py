import grp
import logging
import pwd

from fastapi import Cookie, Request

from . import session as session_store
from .config import settings
from .models import sanitized
from .models.sanitized import SafeStr, SafeUsername

logger = logging.getLogger(__name__)


class NotAuthenticated(Exception):
    pass


@sanitized.enforce
def _user_in_allowed_group(username: SafeUsername) -> bool:
    try:
        user_groups = {g.gr_name for g in grp.getgrall() if username in g.gr_mem}
        # also include primary group
        pw = pwd.getpwnam(username)
        primary_group = grp.getgrgid(pw.pw_gid).gr_name
        user_groups.add(primary_group)
        return bool(user_groups & set(settings.allowed_groups))
    except KeyError:
        return False


def require_auth(request: Request, qm_session: str = Cookie(default=None)) -> SafeUsername:
    if settings.test_auth_user:
        logger.critical(
            "SECURITY: test auth bypass active — request %s %s authenticated as %r without PAM",
            request.method,
            request.url.path,
            settings.test_auth_user,
        )
        return SafeUsername.trusted(settings.test_auth_user, "require_auth:test_bypass")
    if qm_session:
        user = session_store.get_session(SafeStr.of(qm_session, "qm_session"))
        if user:
            return user
    raise NotAuthenticated()
