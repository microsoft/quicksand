"""Opt-in checks of built images against the public eMudhra trust-check endpoints.

Set QUICKSAND_SECURITY_TEST_IMAGE to an image name, native qcow2 path, or saved overlay.
These checks require network access and never update the image under test.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from quicksand_core import NetworkMode, Sandbox
from quicksand_core._types import ResolvedImage
from quicksand_core.qemu.image_resolver import ImageResolver

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_TRUSTED_URL = "https://www.python.org/"
_COMPROMISED_URLS = (
    "https://dv-g3b-emudhra.we-have-a-problem.com/",
    "https://dv-g3b-aaa-emudhra.we-have-a-problem.com/",
)


@contextmanager
def _local_image_files() -> Iterator[None]:
    """Treat staged artifacts as named providers without replacing installed images."""
    resolve_base = ImageResolver._resolve_base_by_name
    original_resolve = ImageResolver.resolve

    def resolve(name: str, *, arch: str | None = None) -> ResolvedImage | None:
        path = Path(name)
        if path.is_file() and path.suffix == ".qcow2":
            kernel = path.with_suffix(".kernel")
            if not kernel.is_file():
                raise FileNotFoundError(kernel)
            initrd = path.with_suffix(".initrd")
            return ResolvedImage(
                name=name,
                chain=[path],
                kernel=kernel,
                initrd=initrd if initrd.is_file() else None,
            )
        if path.is_dir() and (path / "manifest.json").is_file():
            return ImageResolver()._resolve_save(path)
        return resolve_base(name, arch=arch)

    def resolve_image(
        resolver: ImageResolver, image: str, *, arch: str | None = None
    ) -> ResolvedImage:
        result = original_resolve(resolver, image, arch=arch)
        if (Path(image) / "manifest.json").is_file():
            # A saved child must reference the whole staged parent, not just its base.
            return replace(result, name=image)
        return result

    with (
        patch.object(ImageResolver, "_resolve_base_by_name", staticmethod(resolve)),
        patch.object(ImageResolver, "resolve", resolve_image),
    ):
        yield


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sandbox() -> AsyncIterator[Sandbox]:
    image = os.environ.get("QUICKSAND_SECURITY_TEST_IMAGE")
    if not image:
        pytest.skip("Set QUICKSAND_SECURITY_TEST_IMAGE to explicitly select a built image")
    with _local_image_files():
        async with Sandbox(image=image, network_mode=NetworkMode.FULL, memory="2G") as sb:
            yield sb


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def python_sandbox(sandbox: Sandbox) -> Sandbox:
    available = await sandbox.execute("test -x /opt/python/bin/python3")
    if available.exit_code != 0:
        pytest.skip("This image does not include the agent Python environment")
    return sandbox


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def playwright_sandbox(python_sandbox: Sandbox) -> Sandbox:
    probe = (
        'import importlib.util; raise SystemExit(importlib.util.find_spec("playwright") is None)'
    )
    available = await python_sandbox.execute(f"/opt/python/bin/python3 -c {shlex.quote(probe)}")
    if available.exit_code != 0:
        pytest.skip("This image does not include Playwright")
    return python_sandbox


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def firefox_sandbox(sandbox: Sandbox) -> Sandbox:
    available = await sandbox.execute("test -x /usr/bin/firefox-esr")
    if available.exit_code != 0:
        pytest.skip("This image does not include Firefox ESR")
    return sandbox


@pytest.mark.parametrize("url", (_TRUSTED_URL, *_COMPROMISED_URLS))
async def test_system_curl_trust(sandbox: Sandbox, url: str) -> None:
    # Guest DHCP can finish after the agent is ready.
    retry = "--retry 3 --retry-all-errors --retry-delay 1" if url == _TRUSTED_URL else ""
    result = await sandbox.execute(
        f"curl --silent --show-error --max-time 20 --retry-max-time 20 {retry}"
        f" --output /dev/null {shlex.quote(url)}",
        timeout=45,
    )
    expected = 0 if url == _TRUSTED_URL else 60
    assert result.exit_code == expected, result.stderr


@pytest.mark.parametrize("url", (_TRUSTED_URL, *_COMPROMISED_URLS))
async def test_requests_trust(python_sandbox: Sandbox, url: str) -> None:
    script = """
import sys
import requests
try:
    requests.get(sys.argv[1], timeout=20)
except requests.exceptions.SSLError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(60)
"""
    result = await python_sandbox.execute(
        f"/opt/python/bin/python3 -c {shlex.quote(script)} {shlex.quote(url)}",
        timeout=30,
    )
    expected = 0 if url == _TRUSTED_URL else 60
    assert result.exit_code == expected, result.stderr


@pytest.mark.parametrize("url", _COMPROMISED_URLS)
async def test_pip_trust(python_sandbox: Sandbox, url: str) -> None:
    wheel_url = url + "tls_probe-0.0.0-py3-none-any.whl"
    result = await python_sandbox.execute(
        "/opt/python/bin/python3 -m pip --disable-pip-version-check download"
        " --no-index --no-deps --no-cache-dir --only-binary=:all:"
        " --retries 0 --timeout 20 --dest /tmp/quicksand-trust-check"
        f" {shlex.quote(wheel_url)}",
        timeout=30,
    )
    assert result.exit_code != 0
    assert "CERTIFICATE_VERIFY_FAILED" in result.stderr, result.stderr


@pytest.mark.parametrize("url", (_TRUSTED_URL, *_COMPROMISED_URLS))
async def test_playwright_driver_trust(playwright_sandbox: Sandbox, url: str) -> None:
    script = """
import subprocess
import sys
from playwright._impl._driver import compute_driver_executable

node, _ = compute_driver_executable()
javascript = '''
const request = require("node:https").get(process.argv[1], response => response.resume());
request.setTimeout(20000, () => request.destroy(new Error("TLS connection timed out")));
request.on("error", error => {
    console.error(error.code, error.message);
    process.exitCode = 1;
});
'''
raise SystemExit(subprocess.run([node, "-e", javascript, sys.argv[1]], timeout=25).returncode)
"""
    result = await playwright_sandbox.execute(
        f"/opt/python/bin/python3 -c {shlex.quote(script)} {shlex.quote(url)}",
        timeout=35,
    )
    if url == _TRUSTED_URL:
        assert result.exit_code == 0, result.stderr
    else:
        assert result.exit_code != 0
        assert any(
            code in result.stderr
            for code in (
                "CERT_REVOKED",
                "CERT_REJECTED",
                "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
                "SELF_SIGNED_CERT_IN_CHAIN",
            )
        ), result.stderr


@pytest.mark.parametrize("url", (_TRUSTED_URL, *_COMPROMISED_URLS))
async def test_playwright_browser_trust(playwright_sandbox: Sandbox, url: str) -> None:
    script = """
import sys
from playwright.sync_api import Error, sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path="/usr/local/bin/chromium", headless=True)
    try:
        browser.new_page().goto(sys.argv[1], wait_until="commit", timeout=20000)
    except Error as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
    finally:
        browser.close()
"""
    result = await playwright_sandbox.execute(
        f"/opt/python/bin/python3 -c {shlex.quote(script)} {shlex.quote(url)}",
        timeout=35,
    )
    if url == _TRUSTED_URL:
        assert result.exit_code == 0, result.stderr
    else:
        assert result.exit_code != 0
        assert any(
            code in result.stderr
            for code in (
                "net::ERR_CERT_REVOKED",
                "net::ERR_CERT_AUTHORITY_INVALID",
                "net::ERR_CERT_KNOWN_INTERCEPTION_BLOCKED",
                "net::ERR_CERTIFICATE_TRANSPARENCY_REQUIRED",
            )
        ), result.stderr


@pytest.mark.parametrize("url", (_TRUSTED_URL, *_COMPROMISED_URLS))
async def test_firefox_browser_trust(firefox_sandbox: Sandbox, url: str) -> None:
    # Use Firefox's built-in Marionette protocol without installing a test driver.
    script = """
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

with tempfile.TemporaryDirectory() as profile, tempfile.TemporaryFile(mode="w+") as log:
    # Automation's recommended preferences otherwise disable revocation updates.
    Path(profile, "user.js").write_text('user_pref("remote.prefs.recommended", false);\\n')
    browser = subprocess.Popen(
        ["firefox-esr", "--headless", "--no-remote", "--profile", profile, "--marionette"],
        stdout=log, stderr=log,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                connection = socket.create_connection(("127.0.0.1", 2828), timeout=25)
                break
            except ConnectionRefusedError:
                if browser.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Firefox's Marionette endpoint did not become ready")
                time.sleep(0.1)

        with connection, connection.makefile("rb") as stream:
            def receive():
                length = bytearray()
                while True:
                    byte = stream.read(1)
                    if not byte:
                        raise EOFError("Firefox closed its Marionette connection")
                    if byte == b":":
                        return json.loads(stream.read(int(length)))
                    length.extend(byte)

            def command(identifier, name, parameters):
                payload = json.dumps([0, identifier, name, parameters]).encode()
                connection.sendall(str(len(payload)).encode() + b":" + payload)
                response = receive()
                assert response[:2] == [1, identifier], response
                return response[2], response[3]

            assert receive()["marionetteProtocol"] == 3
            error, session = command(1, "WebDriver:NewSession", {
                "capabilities": {"alwaysMatch": {
                    "acceptInsecureCerts": False,
                    "pageLoadStrategy": "eager",
                    "timeouts": {"pageLoad": 20000},
                }},
            })
            assert error is None, error
            assert session["capabilities"]["acceptInsecureCerts"] is False, session
            error, _ = command(2, "WebDriver:Navigate", {"url": sys.argv[1]})
            if error is not None:
                print(json.dumps(error), file=sys.stderr)
                raise SystemExit(60 if error["error"] == "insecure certificate" else 1)
    finally:
        browser.terminate()
        try:
            browser.wait(timeout=10)
        except subprocess.TimeoutExpired:
            browser.kill()
            browser.wait(timeout=5)
        log.seek(0)
        print(log.read(), file=sys.stderr)
"""
    result = await firefox_sandbox.execute(
        f"/usr/bin/python3 -c {shlex.quote(script)} {shlex.quote(url)}",
        timeout=75,
    )
    expected = 0 if url == _TRUSTED_URL else 60
    assert result.exit_code == expected, result.stderr
