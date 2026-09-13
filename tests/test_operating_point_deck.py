import time

import pytest

from copper_mcp.engineering._operating_point_cases import OperatingPointCase
from copper_mcp.engineering._operating_point_deck import (
    OperatingPointDeckError,
    validate_native_diagnostics,
)

LOG = b"""Note: No compatibility mode selected!
Circuit: copper-mcp operating point
ASCII raw file "/work/result.raw"
Doing analysis at TEMP = 25.000000 and TNOM = 27.000000
Using SPARSE 1.3 as Direct Linear Solver
No. of Data Columns : 2
No. of Data Rows : 1
Total analysis time (seconds) = 0.00444548
Total elapsed time (seconds) = 0.021
Total DRAM available = 5910.340 MB.
DRAM currently available = 1345.750 MB.
Maximum ngspice program size =  166.488 MB.
Current ngspice program size =   24.000 MB.
Shared ngspice pages =   13.199 MB.
Text (code) pages =    1.633 MB.
Stack = 0 bytes.
Library pages =  141.258 MB.
"""


def test_actual_native_log_allows_numeric_column_padding():
    validate_native_diagnostics(
        LOG, OperatingPointCase("sample", 25000, "0", (), ()), 2, deadline=time.monotonic() + 5
    )


@pytest.mark.parametrize(
    "payload",
    (
        LOG + b"Warning: owned error\n",
        LOG.replace(b"Columns : 2", b"Columns : 3"),
        LOG.replace(b"Rows : 1", b"Rows : 0"),
        LOG.replace(b"TEMP = 25", b"TEMP = 35"),
        LOG.replace(b"141.258", b"nan"),
    ),
)
def test_unknown_failed_or_mismatched_log_refuses(payload):
    with pytest.raises(OperatingPointDeckError):
        validate_native_diagnostics(
            payload,
            OperatingPointCase("sample", 25000, "0", (), ()),
            2,
            deadline=time.monotonic() + 5,
        )
