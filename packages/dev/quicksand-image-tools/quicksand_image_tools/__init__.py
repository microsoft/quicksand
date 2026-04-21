"""
Quicksand Image Tools: Build custom VM images from Dockerfiles.

This package provides tools for building custom quicksand VM images.

Quick start:
    from quicksand_image_tools import build_image

    # Build from a Dockerfile
    image_path = build_image("./Dockerfile")

The Rust agent source is included in this package at quicksand-guest-agent/.
Image packages use symlinks to reference it for multi-stage Docker builds.
"""

from .build import build_image, get_agent_source_dir

__all__ = ["build_image", "get_agent_source_dir"]

__version__ = "0.1.0"
