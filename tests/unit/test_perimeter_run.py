"""ELMFIRE 화선 출발 실행 폴더의 namelist 편집."""

from spread.perimeter_run import edit_namelist

DECK = """&INPUTS
FUELS_AND_TOPOGRAPHY_DIRECTORY = './inputs'
WEATHER_DIRECTORY       = './inputs'
/
&OUTPUTS
OUTPUTS_DIRECTORY       = './outputs_test6h'
DUMP_SPREAD_RATE        = .TRUE.
/
&TIME_CONTROL
SIMULATION_TSTART       = 1440.0     ! 11:24
SIMULATION_TSTOP        = 23040.0    ! 6h
/
&SIMULATOR
NUM_IGNITIONS           = 2
X_IGN(1)                = 1098492.2  ! a
Y_IGN(1)                = 1819712.0
T_IGN(1)                = 1440.0
X_IGN(2)                = 1085295.0
Y_IGN(2)                = 1824255.0
T_IGN(2)                = 13140.0
/
&MISCELLANEOUS
SCRATCH                        = './scratch'
MISCELLANEOUS_INPUTS_DIRECTORY = './inputs'
/
"""


def test_perimeter_start_has_no_ignitions():
    out = edit_namelist(DECK, 6600.0, 35400.0, None)
    assert "NUM_IGNITIONS           = 0" in out
    assert "X_IGN" not in out and "T_IGN" not in out
    assert "SIMULATION_TSTART       = 6600.0" in out and "SIMULATION_TSTOP        = 35400.0" in out
    assert "OUTPUTS_DIRECTORY       = './outputs'" in out
    assert "DUMP_SPREAD_RATE        = .FALSE." in out


def test_point_start_keeps_one_ignition():
    out = edit_namelist(DECK, 1440.0, 6600.0, (1.0, 2.0))
    assert out.count("X_IGN(") == 1 and "T_IGN(1)                = 1440.0" in out
