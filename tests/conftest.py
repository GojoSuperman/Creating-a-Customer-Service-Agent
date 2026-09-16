from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def modumall_dir() -> Path:
    return ROOT / "domains" / "modumall"


@pytest.fixture(scope="module")
def modumall_dir_module() -> Path:
    return ROOT / "domains" / "modumall"
