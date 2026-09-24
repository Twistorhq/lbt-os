"""Leak-detection engine — TW-201.

One engine, twelve aim-points. Detectors are plugins bound to verticals;
a new vertical ships new detectors, never a new engine.
"""
from .engine import run_leak_scan, vertical_for_industry
from .registry import Detector, all_detectors, detectors_for

__all__ = ["Detector", "all_detectors", "detectors_for", "run_leak_scan", "vertical_for_industry"]
