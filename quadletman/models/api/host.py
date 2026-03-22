from pydantic import BaseModel

from ..sanitized import SafeStr, enforce_model_safety


@enforce_model_safety
class HostSettingUpdate(BaseModel):
    key: SafeStr
    value: SafeStr


@enforce_model_safety
class SELinuxBooleanUpdate(BaseModel):
    name: SafeStr
    enabled: bool
