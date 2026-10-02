import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("preamble_sync", "test_preamble_sync", ["rtl/rx/frontend/preamble_sync.sv"]))
