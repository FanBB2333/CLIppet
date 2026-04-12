"""Tests for packaging metadata validation."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_uses_single_version_source() -> None:
    """Ensure version is sourced dynamically from clippet/__init__.py."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "clippet/__init__.py"


def test_pyproject_declares_release_metadata() -> None:
    """Ensure all required release metadata fields are present."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    
    assert project["name"] == "clippet"
    assert project["readme"] == "README.md"
    assert "Repository" in project["urls"]
    assert "Homepage" in project["urls"]


def test_pyproject_has_classifiers() -> None:
    """Ensure classifiers are present for PyPI display."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    
    assert "classifiers" in project
    classifiers = project["classifiers"]
    
    # Check for key classifiers
    assert any("Python :: 3.10" in c for c in classifiers)
    assert any("MIT" in c for c in classifiers)


def test_pyproject_has_authors() -> None:
    """Ensure authors are specified."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    
    assert "authors" in project
    assert len(project["authors"]) > 0
