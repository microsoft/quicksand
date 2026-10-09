"""Unit tests for ``quicksand run`` port-forward argument parsing."""

from __future__ import annotations

import pytest
from quicksand.cli.run import _parse_forward
from quicksand_core import Forward, Guest, Host


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("8080:80", Forward(Host(8080), Guest(80))),
        ("0.0.0.0:8080:80", Forward(Host(8080, "0.0.0.0"), Guest(80))),
        ("8080:10.0.2.15:80", Forward(Host(8080), Guest(80, "10.0.2.15"))),
        ("0.0.0.0:8080:10.0.2.15:80", Forward(Host(8080, "0.0.0.0"), Guest(80, "10.0.2.15"))),
    ],
)
def test_parse_host_to_guest(spec, expected):
    assert _parse_forward(spec, guest_to_host=False) == expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("3128:8080", Forward(Guest(3128), Host(8080))),
        ("10.0.2.102:3128:8080", Forward(Guest(3128, "10.0.2.102"), Host(8080))),
        ("3128:localhost:8080", Forward(Guest(3128), Host(8080, "localhost"))),
        (
            "10.0.2.102:3128:localhost:8080",
            Forward(Guest(3128, "10.0.2.102"), Host(8080, "localhost")),
        ),
    ],
)
def test_parse_guest_to_host(spec, expected):
    assert _parse_forward(spec, guest_to_host=True) == expected


@pytest.mark.parametrize("spec", ["3128", "a:b", "1:2:3:4:5", "3128:0", "10.0.2.2:80:8080"])
def test_parse_guest_to_host_rejects(spec):
    with pytest.raises(ValueError):
        _parse_forward(spec, guest_to_host=True)


@pytest.mark.parametrize("spec", ["80", "localhost:8080:80", "8080:guest.local:80"])
def test_parse_host_to_guest_rejects(spec):
    with pytest.raises(ValueError):
        _parse_forward(spec, guest_to_host=False)
