"""
Where a transcription request may go for a restricted document.

The provider label "local" only exempts a backend from dollar accounting.
It does not make its URL local. A document with never_send_offsite may only
be sent to a literal loopback address or the hostname ``localhost``.

This is the same classification as ``external_htr_policy.literal_local``.
Runtime modules may not import that module (see its isolation tests), so the
rule is restated here and a parity test keeps the two identical.
Classification never resolves DNS. Anything unparseable is off-site.
"""
from __future__ import annotations

import ipaddress
import urllib.parse


def endpoint_is_loopback(endpoint_url) -> bool:
    """True only for http(s) to literal loopback or ``localhost``.

    Userinfo, a query, or a fragment makes a URL off-site. 127.0.0.0/8 and
    ::1 count; private ranges, 0.0.0.0, and IPv4-mapped IPv6 do not.
    """
    if not isinstance(endpoint_url, str):
        return False
    parts = urllib.parse.urlsplit(endpoint_url.strip())
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.query
            or parts.fragment):
        return False
    host = parts.hostname
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if getattr(address, "ipv4_mapped", None) is not None:
        return False
    return address.is_loopback is True


def restricted_verdict(policy, endpoint_url):
    """None when the call may go; otherwise 'offsite' or 'policy'.

    No policy row, or never_send_offsite False: allowed. True: only a
    loopback endpoint. Any other flag value fails closed.
    """
    if policy is None:
        return None
    flag = getattr(policy, "never_send_offsite", None)
    if flag is False:
        return None
    if flag is not True:
        return "policy"
    return None if endpoint_is_loopback(endpoint_url) else "offsite"
