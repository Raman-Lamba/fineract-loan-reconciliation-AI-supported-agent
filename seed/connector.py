"""Loads the project's .env, then imports the existing openMF connector (vendor/, unmodified)."""
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "vendor" / "mcp-mifosx" / "python"

if not CONNECTOR.exists():
    sys.exit("Connector not found in vendor/mcp-mifosx. Run .\\start.ps1 first.")
load_dotenv(ROOT / ".env")  # before the import: the adapter reads MIFOSX_* when it is created
sys.path.insert(0, str(CONNECTOR))

from tools.mcp_adapter import fineract_client  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)  # the adapter logs every call at INFO


def ok(response, what: str):
    """Return the response, or stop with Fineract's own error message."""
    if isinstance(response, dict) and "error" in response:
        sys.exit(f"{what} failed: {response['error']}")
    return response
