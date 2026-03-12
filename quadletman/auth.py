import grp
import logging
import pwd

import pam
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

logger = logging.getLogger(__name__)
security = HTTPBasic()


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


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    p = pam.pam()
    if not p.authenticate(credentials.username, credentials.password):
        logger.warning("Authentication failed for user: %s", credentials.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="quadletman"'},
        )
    if not _user_in_allowed_group(credentials.username):
        logger.warning(
            "Authorization failed for user %s: not in allowed groups %s",
            credentials.username,
            settings.allowed_groups,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User is not in an authorized group: {settings.allowed_groups}",
        )
    logger.info("Authenticated user: %s", credentials.username)
    return credentials.username
