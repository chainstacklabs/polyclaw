"""Make `lib` and `scripts` importable and keep tests off the real home dir."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# lib.position_storage resolves ~/.openclaw at import time; point HOME at a
# throwaway dir so tests never touch real positions or wallet cache.
os.environ["HOME"] = tempfile.mkdtemp(prefix="polyclaw-test-")
for var in ("POLYCLAW_PRIVATE_KEY", "POLYCLAW_RELAYER_API_KEY", "POLYCLAW_MAX_TRADE_USD"):
    os.environ.pop(var, None)
