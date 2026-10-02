import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("despreader", "test_despreader", ["rtl/rx/backend/despreader.sv"]))
