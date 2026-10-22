"""Build-time browser distrust for the compromised eMudhra intermediate CAs.

Certificate source: https://emudhra.we-have-a-problem.com/impacted-intermediates.json
Chromium blocks by SPKI; Firefox's NSS trust records require each certificate,
including the cross-signed variants.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path


def _distrusted_certificates() -> list[str]:
    data = Path(__file__).with_name("compromised_ca_certificates.json").read_text()
    return json.loads(data)["certificates"]


async def install_chromium_ca_distrust(
    shell: Callable[..., Awaitable[None]], policy_directory: str
) -> None:
    """Block compromised keys even when browser revocation updates are disabled."""
    directory = shlex.quote(policy_directory)
    destination = shlex.quote(f"{policy_directory}/quicksand-ca-distrust.json")
    policy = json.dumps({"CADistrustedCertificates": _distrusted_certificates()}).encode()
    await shell(
        f"mkdir -p {directory} && cat > {destination}"
        f" && chown 0:0 {directory} {destination}"
        f" && chmod 755 {directory} && chmod 644 {destination}",
        stdin=policy,
    )


async def install_firefox_ca_distrust(
    shell: Callable[..., Awaitable[None]], firefox_directory: str
) -> None:
    """Distrust compromised CAs in every ESR profile before online revocation updates."""
    configuration = (
        "// Quicksand: distrust compromised eMudhra intermediate certificates.\n"
        'var certdb = Components.classes["@mozilla.org/security/x509certdb;1"]\n'
        "    .getService(Components.interfaces.nsIX509CertDB);\n"
        f"for (var certificate of {json.dumps(_distrusted_certificates())}) {{\n"
        '    certdb.addCertFromBase64(certificate, "p,p,p");\n'
        "}\n"
    )
    config_path = shlex.quote(f"{firefox_directory}/quicksand-ca-distrust.cfg")
    await shell(
        f"cat > {config_path} && chown 0:0 {config_path} && chmod 644 {config_path}",
        stdin=configuration.encode(),
    )
    preferences = (
        'pref("general.config.filename", "quicksand-ca-distrust.cfg");\n'
        'pref("general.config.obscure_value", 0);\n'
    )
    directory = shlex.quote(f"{firefox_directory}/defaults/pref")
    destination = shlex.quote(f"{firefox_directory}/defaults/pref/quicksand-ca-distrust.js")
    await shell(
        f"mkdir -p {directory} && cat > {destination}"
        f" && chown 0:0 {directory} {destination}"
        f" && chmod 755 {directory} && chmod 644 {destination}",
        stdin=preferences.encode(),
    )
