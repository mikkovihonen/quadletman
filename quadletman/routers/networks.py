"""Network CRUD routes."""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..i18n import gettext as _t
from ..models import NetworkCreate
from ..models.constraints import FieldChoices
from ..models.sanitized import (
    SafeAbsPathOrEmpty,
    SafeFormBool,
    SafeIdentifier,
    SafeIpAddress,
    SafeNetDriver,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeUnitName,
    SafeUsername,
    SafeUUID,
)
from ..podman_version import get_features
from ..services import compartment_manager, user_manager
from ..services.compartment_manager import ServiceCondition
from .helpers import (
    choices_for_template,
    comp_ctx,
    is_htmx,
    require_auth,
    require_compartment,
    toast_trigger,
    validate_version_spans,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/compartments/{compartment_id}/networks/{network_id}/form")
async def network_edit_form(
    request: Request,
    compartment_id: SafeSlug,
    network_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    network = await compartment_manager.get_network(db, network_id)
    if comp is None or network is None:
        raise HTTPException(status_code=404, detail=_t("Network not found"))
    net_drivers, _ = user_manager.get_compartment_drivers(compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/network_form.html",
        {
            "compartment": comp,
            "network": network,
            "net_driver_choices": choices_for_template(
                FieldChoices(dynamic=True, empty_label="default"),
                current_value=network.driver,
                dynamic_items=list(net_drivers),
            ),
        },
    )


@router.post("/api/compartments/{compartment_id}/networks", status_code=status.HTTP_201_CREATED)
async def create_network(
    request: Request,
    compartment_id: SafeSlug,
    qm_name: SafeResourceName = Form(...),
    driver: SafeNetDriver = Form(""),
    subnet: SafeIpAddress = Form(""),
    gateway: SafeIpAddress = Form(""),
    ipv6: SafeFormBool = Form(""),
    internal: SafeFormBool = Form(""),
    dns_enabled: SafeFormBool = Form(""),
    disable_dns: SafeFormBool = Form(""),
    ip_range: SafeIpAddress = Form(""),
    options: SafeStr = Form(""),
    containers_conf_module: SafeAbsPathOrEmpty = Form(""),
    ipam_driver: SafeIdentifier = Form(""),
    dns: SafeIpAddress = Form(""),
    service_name: SafeUnitName = Form(""),
    network_delete_on_stop: SafeFormBool = Form(""),
    interface_name: SafeIdentifier = Form(""),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    data = NetworkCreate(
        qm_name=qm_name,
        driver=driver,
        subnet=subnet,
        gateway=gateway,
        ipv6=ipv6 == "true",
        internal=internal == "true",
        dns_enabled=dns_enabled == "true",
        disable_dns=disable_dns == "true",
        ip_range=ip_range,
        options=options,
        containers_conf_module=containers_conf_module,
        ipam_driver=ipam_driver,
        dns=dns,
        service_name=service_name,
        network_delete_on_stop=network_delete_on_stop == "true",
        interface_name=interface_name,
    )
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)

    try:
        network = await compartment_manager.add_network(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _t("A network named '%(name)s' already exists") % {"name": qm_name},
        ) from exc
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.exception("Failed to add network")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, _t("Internal server error")
        ) from exc

    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger(_t("Network created")),
        )
    return network.model_dump()


@router.put("/api/compartments/{compartment_id}/networks/{network_id}")
async def update_network(
    request: Request,
    compartment_id: SafeSlug,
    network_id: SafeUUID,
    data: NetworkCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)

    try:
        network = await compartment_manager.update_network(db, compartment_id, network_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _t("A network named '%(name)s' already exists") % {"name": data.qm_name},
        ) from exc
    if network is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Network not found"))

    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger(_t("Network updated")),
        )
    return network.model_dump()


@router.delete(
    "/api/compartments/{compartment_id}/networks/{network_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_network(
    request: Request,
    compartment_id: SafeSlug,
    network_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    try:
        await compartment_manager.delete_network(db, compartment_id, network_id)
    except ValueError as exc:
        logger.warning("Network deletion conflict: %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger(_t("Network deleted")),
        )
