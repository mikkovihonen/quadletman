from typing import Annotated

from pydantic import BaseModel

from ..constraints import N_, FieldConstraints
from ..sanitized import SafeStr, enforce_model_safety


@enforce_model_safety
class HostSettingUpdate(BaseModel):
    key: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Kernel parameter name"),
            label_hint=N_("e.g. net.ipv4.ip_forward"),
            placeholder=N_("net.ipv4.ip_forward"),
        ),
    ]
    value: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Kernel parameter value"),
            label_hint=N_("setting value"),
            placeholder=N_("1"),
        ),
    ]


@enforce_model_safety
class SELinuxBooleanUpdate(BaseModel):
    name: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("SELinux boolean name"),
            label_hint=N_("boolean name"),
        ),
    ]
    enabled: Annotated[
        bool,
        FieldConstraints(
            description=N_("Whether this boolean is enabled"),
            label_hint=N_("persists across reboots"),
        ),
    ]
