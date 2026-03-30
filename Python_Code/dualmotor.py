#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Angle (0~75 deg) -> RIGHT motor (3200~4072), LEFT motor (2580~3451)
- Right:
    angle = 0 deg  -> RIGHT = 4072 (MAX)
    angle = 75 deg -> RIGHT = 3200 (MIN)
- Left (from Right mapping):
    RIGHT=4072 -> LEFT=2580
    RIGHT=3200 -> LEFT=3451
"""

from typing import Tuple, Union

# ---- Right motor mapping ----
RIGHT_MAX = 4072  # at 0 deg
RIGHT_MIN = 3200  # at 75 deg
ANGLE_MIN = 0.0
ANGLE_MAX = 75.0

# ---- Left motor mapping (linear from Right) ----
LEFT_AT_RIGHT_MAX = 2580
LEFT_AT_RIGHT_MIN = 3451

Number = Union[int, float]

def _clamp(x: Number, lo: Number, hi: Number) -> Number:
    return lo if x < lo else hi if x > hi else x

def angle_to_right_value(angle_deg: Number) -> int:
    """Map angle [0,75] to RIGHT 12-bit value [3200,4072] linearly."""
    a = float(_clamp(angle_deg, ANGLE_MIN, ANGLE_MAX))
    right_val = RIGHT_MAX + (RIGHT_MIN - RIGHT_MAX) * (a / ANGLE_MAX)
    return int(round(right_val))

def right_to_left_value(right_val: Number) -> int:
    """Convert RIGHT value to LEFT value using linear mapping."""
    a = (LEFT_AT_RIGHT_MIN - LEFT_AT_RIGHT_MAX) / (RIGHT_MIN - RIGHT_MAX)
    b = LEFT_AT_RIGHT_MAX - a * RIGHT_MAX
    left_val = a * float(right_val) + b
    return int(round(left_val))

def angle_to_left_value(angle_deg: Number) -> int:
    """Angle -> LEFT by chaining Right mapping."""
    return right_to_left_value(angle_to_right_value(angle_deg))

def angle_to_both(angle_deg: Number) -> Tuple[int, int]:
    """Return (RIGHT, LEFT) motor values for given angle."""
    r = angle_to_right_value(angle_deg)
    l = right_to_left_value(r)
    return r, l

def angles_to_soft_limits(angle_min_deg: Number, angle_max_deg: Number) -> Tuple[Tuple[int,int], Tuple[int,int]]:
    """
    Convert desired [angle_min, angle_max] range to (RIGHT_min, RIGHT_max), (LEFT_min, LEFT_max) numeric limits.
    Note: RIGHT code decreases with angle; LEFT code increases with angle.
    Returns:
        ((RIGHT_MIN_NUM, RIGHT_MAX_NUM), (LEFT_MIN_NUM, LEFT_MAX_NUM))
    """
    r1 = angle_to_right_value(angle_min_deg)
    r2 = angle_to_right_value(angle_max_deg)
    l1 = right_to_left_value(r1)
    l2 = right_to_left_value(r2)

    right_min_num = min(r1, r2)
    right_max_num = max(r1, r2)
    left_min_num  = min(l1, l2)
    left_max_num  = max(l1, l2)
    return (right_min_num, right_max_num), (left_min_num, left_max_num)
