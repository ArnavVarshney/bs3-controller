"""singleton guard tests: distinct names coexist; same name is exclusive
in-process on Windows (mutex) — POSIX flock is per-fd so same-process
re-acquire is only asserted on nt."""

import os

import pytest

from bs3 import singleton


def test_distinct_names_coexist():
    assert singleton.acquire("bs3-test-a") is True
    assert singleton.acquire("bs3-test-b") is True


@pytest.mark.skipif(os.name != "nt", reason="mutex semantics are Windows-only")
def test_same_name_exclusive_windows():
    assert singleton.acquire("bs3-test-c") is True
    assert singleton.acquire("bs3-test-c") is False
