import tomllib
from pathlib import Path

from btc_sentinel import __version__


def test_package_version_matches_project_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == metadata["project"]["version"]
