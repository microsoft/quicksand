"""Tests for quicksand.cli.install release-tag filtering."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

from quicksand.cli.install import (
    _get_latest_release_tag,
    _is_stable_release_tag,
    _resolve_compatible_tag,
)


class TestIsStableReleaseTag:
    def test_accepts_stable_version(self):
        assert _is_stable_release_tag("quicksand-qemu/v0.5.9", "quicksand-qemu")

    def test_accepts_four_segment_version(self):
        assert _is_stable_release_tag("quicksand-qemu/v1.2.3.4", "quicksand-qemu")

    def test_rejects_alpha_prerelease(self):
        assert not _is_stable_release_tag("quicksand-qemu/v0.6.0a1", "quicksand-qemu")

    def test_rejects_beta_prerelease(self):
        assert not _is_stable_release_tag("quicksand-qemu/v0.6.0b2", "quicksand-qemu")

    def test_rejects_rc_prerelease(self):
        assert not _is_stable_release_tag("quicksand-qemu/v0.6.0rc1", "quicksand-qemu")

    def test_rejects_dev_version(self):
        assert not _is_stable_release_tag("quicksand-qemu/v0.6.0.dev1", "quicksand-qemu")

    def test_rejects_base_suffix(self):
        assert not _is_stable_release_tag("quicksand-qemu/v0.6.0-base", "quicksand-qemu")

    def test_rejects_dev_suffix(self):
        assert not _is_stable_release_tag("quicksand-qemu/v0.6.0-dev", "quicksand-qemu")

    def test_rejects_other_package(self):
        # Prefix mismatch — tag belongs to a different package name.
        assert not _is_stable_release_tag("quicksand-qemu-base/v0.6.0", "quicksand-qemu")

    def test_rejects_tag_without_prefix(self):
        assert not _is_stable_release_tag("v0.6.0", "quicksand-qemu")


def _mock_urlopen(payload: object):
    """Return a context manager that yields a response with the given JSON body."""

    class _Resp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return BytesIO(self._body)

        def __exit__(self, *_):
            return False

    return _Resp(json.dumps(payload).encode())


class TestGetLatestReleaseTag:
    def test_picks_latest_stable_skipping_prereleases(self):
        # matching-refs returns refs in ascending order; tags[-1] picks the last.
        refs = [
            {"ref": "refs/tags/quicksand-qemu/v0.5.8"},
            {"ref": "refs/tags/quicksand-qemu/v0.5.9"},
            {"ref": "refs/tags/quicksand-qemu/v0.6.0a1"},
            {"ref": "refs/tags/quicksand-qemu/v0.6.0.dev1"},
            {"ref": "refs/tags/quicksand-qemu/v0.5.10.dev0-base"},
        ]
        with patch(
            "quicksand.cli.install.urllib.request.urlopen",
            return_value=_mock_urlopen(refs),
        ):
            tag = _get_latest_release_tag("quicksand-qemu")
        assert tag == "quicksand-qemu/v0.5.9"

    def test_ignores_unrelated_prefix(self):
        # GitHub's matching-refs API matches by string prefix on the ref,
        # so `quicksand-qemu` could also match `quicksand-qemu-base`. Make
        # sure the package filter drops the unrelated one.
        refs = [
            {"ref": "refs/tags/quicksand-qemu-base/v0.6.0"},
            {"ref": "refs/tags/quicksand-qemu/v0.5.9"},
        ]
        with patch(
            "quicksand.cli.install.urllib.request.urlopen",
            return_value=_mock_urlopen(refs),
        ):
            tag = _get_latest_release_tag("quicksand-qemu")
        assert tag == "quicksand-qemu/v0.5.9"

    def test_returns_none_when_only_prereleases(self):
        refs = [
            {"ref": "refs/tags/quicksand-qemu/v0.6.0a1"},
            {"ref": "refs/tags/quicksand-qemu/v0.6.0b2"},
        ]
        with patch(
            "quicksand.cli.install.urllib.request.urlopen",
            return_value=_mock_urlopen(refs),
        ):
            tag = _get_latest_release_tag("quicksand-qemu")
        assert tag is None


class TestResolveCompatibleTag:
    def test_skips_prerelease_when_choosing_baseline(self):
        # quicksand release on 2026-05-07. 0.6.0a1 was published the day
        # before but must be ignored — baseline should fall back to 0.5.9.
        all_releases = {
            "quicksand-qemu/v0.5.8": "2026-04-20T00:00:00Z",
            "quicksand-qemu/v0.5.9": "2026-05-01T00:00:00Z",
            "quicksand-qemu/v0.6.0a1": "2026-05-06T00:00:00Z",
            "quicksand-qemu/v0.6.0.dev1": "2026-05-05T00:00:00Z",
        }
        tag = _resolve_compatible_tag("quicksand-qemu", "2026-05-07T00:00:00Z", all_releases)
        assert tag == "quicksand-qemu/v0.5.9"

    def test_picks_newest_patch_in_baseline_series(self):
        # Patches published after quicksand's release date are still allowed
        # as long as they share the baseline's major.minor.
        all_releases = {
            "quicksand-qemu/v0.5.8": "2026-04-20T00:00:00Z",
            "quicksand-qemu/v0.5.9": "2026-05-01T00:00:00Z",
            "quicksand-qemu/v0.5.10": "2026-06-01T00:00:00Z",
            "quicksand-qemu/v0.6.0a1": "2026-05-15T00:00:00Z",
            "quicksand-qemu/v0.6.0": "2026-06-15T00:00:00Z",
        }
        tag = _resolve_compatible_tag("quicksand-qemu", "2026-05-07T00:00:00Z", all_releases)
        assert tag == "quicksand-qemu/v0.5.10"

    def test_returns_none_when_no_stable_releases(self):
        all_releases = {
            "quicksand-qemu/v0.6.0a1": "2026-05-06T00:00:00Z",
            "quicksand-qemu/v0.6.0-base": "2026-05-06T00:00:00Z",
        }
        tag = _resolve_compatible_tag("quicksand-qemu", "2026-05-07T00:00:00Z", all_releases)
        assert tag is None
