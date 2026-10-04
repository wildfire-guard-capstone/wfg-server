"""Unit conversions between SI (our data) and the US customary units ELMFIRE expects.

ELMFIRE wind speed inputs are in mph; fuel_models.csv uses lb/ft2, 1/ft, ft and BTU/lb.
A single wrong conversion changes the rate of spread several-fold, so every
conversion goes through this module and is unit-tested.
"""

MPH_PER_MS = 2.2369362920544
LBFT2_PER_KGM2 = 0.204816143
FT_PER_M = 3.280839895
INV_FT_PER_INV_CM = 30.48
BTULB_PER_KJKG = 0.429922614


def ms_to_mph(v: float) -> float:
    return v * MPH_PER_MS


def mph_to_ms(v: float) -> float:
    return v / MPH_PER_MS


def kgm2_to_lbft2(v: float) -> float:
    return v * LBFT2_PER_KGM2


def m_to_ft(v: float) -> float:
    return v * FT_PER_M


def inv_cm_to_inv_ft(v: float) -> float:
    """Surface-area-to-volume ratio: cm2/cm3 (= 1/cm) -> 1/ft."""
    return v * INV_FT_PER_INV_CM


def kjkg_to_btulb(v: float) -> float:
    return v * BTULB_PER_KJKG
