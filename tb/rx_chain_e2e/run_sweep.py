import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import run

run('rx_chain_e2e', 'test_cfo_sweep', [
    'tb/rx_chain_e2e/e2e_top.sv',
    'rtl/common/half_sine_fir.sv',
    'rtl/rx/rx_matched_filter.sv',
    'rtl/rx/preamble_sync.sv',
    'rtl/rx/despreader.sv',
    'rtl/rx/rx_deframer.sv',
    'rtl/common/pn9_whiten.sv', 'rtl/common/crc16_fcs.sv',
])
