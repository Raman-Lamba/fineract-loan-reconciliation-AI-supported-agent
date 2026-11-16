"""Confirms the Fineract login and lists one client and one loan through the connector."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seed.connector import fineract_client as fc, ok  # noqa: E402

auth = ok(fc.execute_post("authentication", {"username": fc.username, "password": fc.password}), "Login")
print(f"login: authenticated={auth.get('authenticated')} user={auth.get('username')}")

for name in ("clients", "loans"):
    page = ok(fc.execute_get(name, {"limit": 1}), f"List {name}")
    items = page.get("pageItems", [])
    total = page.get("totalFilteredRecords")
    if items:
        first = items[0]
        label = first.get("displayName") or first.get("clientName")
        print(f"{name}: total={total} first: id={first.get('id')} account={first.get('accountNo')} name={label}")
    else:
        print(f"{name}: none yet (run: python -m seed.load_fineract)")
