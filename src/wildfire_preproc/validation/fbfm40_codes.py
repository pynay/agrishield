"""FBFM40 (Scott & Burgan 40) fuel-model code catalog."""

from __future__ import annotations

# Non-burnable codes (Scott & Burgan, 2005).
NON_BURNABLE_CODES: frozenset[int] = frozenset({91, 92, 93, 98, 99})

# Full set of valid burnable codes
# (grass, grass-shrub, shrub, timber-understory, timber-litter, slash-blowdown).
BURNABLE_CODES: frozenset[int] = frozenset(
    {
        # GR group
        101, 102, 103, 104, 105, 106, 107, 108, 109,
        # GS group
        121, 122, 123, 124,
        # SH group
        141, 142, 143, 144, 145, 146, 147, 148, 149,
        # TU group
        161, 162, 163, 164, 165,
        # TL group
        181, 182, 183, 184, 185, 186, 187, 188, 189,
        # SB group
        201, 202, 203, 204,
    }
)

VALID_FBFM40_CODES: frozenset[int] = NON_BURNABLE_CODES | BURNABLE_CODES
