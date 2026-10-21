"""Decide whether an external HTR call may leave the machine.

A missing policy row is allowed. ``never_send_offsite is True`` allows
only a literal loopback address or the hostname ``localhost``. Other
destinations are off-site. Classification does not resolve DNS. A lookup
that fails, or a flag that is not a bool, fails closed.
"""
from __future__ import annotations

import ipaddress
import urllib.parse

CODE_OFFSITE = "offsite_forbidden"
CODE_POLICY = "policy_unreadable"
CODE_INVALID = "invalid_request"

MESSAGE_OFFSITE = "document forbids an off-site engine"
MESSAGE_POLICY = "document policy could not be checked"


def literal_local(endpoint_url) -> bool:
    """True when the base URL host is a literal loopback or localhost.

    Userinfo, a query, a fragment, and a non-http scheme are not local.
    ``127.0.0.0/8`` and ``::1`` count. Private addresses, ``0.0.0.0``,
    and IPv4-mapped IPv6 addresses do not.
    """
    host = _host(endpoint_url)
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # Python 3.12.15 reports an IPv4-mapped loopback as is_loopback.
    # A mapped address is still off-site.
    if getattr(address, "ipv4_mapped", None) is not None:
        return False
    return address.is_loopback is True


def policy_verdict(policy, endpoint_url):
    """Return a fixed code, or None when the call may continue.

    None for the policy object means the document has no policy row.
    """
    if policy is None:
        return None
    flag = getattr(policy, "never_send_offsite", None)
    if flag is False:
        return None
    if flag is not True:
        return CODE_POLICY
    if literal_local(endpoint_url):
        return None
    return CODE_OFFSITE


def request_owners(payload):
    """Return document id, part id, and line ids, or None.

    Ids are the contract's non-negative integers or non-empty strings.
    A bool is rejected. The image bytes are not read.
    """
    if not isinstance(payload, dict):
        return None
    document_id = _identity(payload.get("document_id"))
    part_id = _identity(payload.get("part_id"))
    lines = payload.get("lines")
    if document_id is None or part_id is None or not isinstance(lines, list) or not lines:
        return None
    collected = []
    for item in lines:
        if not isinstance(item, dict):
            return None
        line_id = _identity(item.get("line_id"))
        if line_id is None:
            return None
        collected.append(line_id)
    if len(collected) != len(set(collected)):
        return None
    return document_id, part_id, tuple(collected)


def owners_match(document_id, part_id, line_ids, *, document_pk, part_pk, line_pks) -> bool:
    """True when every supplied id is one of the trusted primary keys."""
    if _pk_text(document_pk) != document_id or _pk_text(part_pk) != part_id:
        return False
    if not isinstance(line_ids, tuple) or not line_ids:
        return False
    trusted = set()
    for pk in line_pks:
        text = _pk_text(pk)
        if text is None:
            return False
        trusted.add(text)
    return all(item in trusted for item in line_ids)


def review_loaded(config, request, *, load_part, load_line_pks, load_policy):
    """Apply identity and policy checks to records the loaders return.

    A loader exception becomes a fixed code. The exception text is dropped.
    """
    parsed = request_owners(request)
    if parsed is None:
        return CODE_INVALID
    document_id, part_id, line_ids = parsed
    try:
        part = load_part(part_id)
        line_pks = load_line_pks(part)
    except Exception:
        return CODE_INVALID
    if not owners_match(
        document_id,
        part_id,
        line_ids,
        document_pk=getattr(part, "document_id", None),
        part_pk=getattr(part, "pk", None),
        line_pks=line_pks,
    ):
        return CODE_INVALID
    try:
        policy = load_policy(part)
    except Exception:
        return CODE_POLICY
    return policy_verdict(policy, getattr(config, "endpoint_url", None))


def review_live_request(config, request):
    """Load the part, its lines, and the policy, then decide.

    This is the database adapter. Tests of the decision use ``review_loaded``
    with fakes because this function imports Django.
    """
    from ai.models import AIDocumentPolicy
    from core.models import DocumentPart, Line

    def load_part(part_id):
        return DocumentPart.objects.only("id", "document_id").get(pk=part_id)

    def load_line_pks(part):
        return list(
            Line.objects.filter(document_part_id=part.pk).values_list("pk", flat=True)
        )

    def load_policy(part):
        return (
            AIDocumentPolicy.objects.filter(document_id=part.document_id)
            .only("never_send_offsite")
            .first()
        )

    return review_loaded(
        config,
        request,
        load_part=load_part,
        load_line_pks=load_line_pks,
        load_policy=load_policy,
    )


def review_document_endpoint(document, config):
    """Load the document policy and decide from the stored endpoint.

    The document object comes from the part row the command already loaded.
    """
    document_pk = getattr(document, "pk", None)
    if isinstance(document_pk, bool) or document_pk is None:
        return CODE_INVALID
    try:
        from ai.models import AIDocumentPolicy
        policy = (
            AIDocumentPolicy.objects.filter(document_id=document_pk)
            .only("never_send_offsite")
            .first()
        )
    except Exception:
        return CODE_POLICY
    return policy_verdict(policy, getattr(config, "endpoint_url", None))


def _host(endpoint_url):
    if not isinstance(endpoint_url, str):
        return None
    parts = urllib.parse.urlsplit(endpoint_url.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        return None
    return parts.hostname


def _identity(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value < 0:
            return None
        return str(value)
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None


def _pk_text(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            return None
        return str(value)
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None
