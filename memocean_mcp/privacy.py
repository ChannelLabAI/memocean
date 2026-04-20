"""
privacy.py — Ocean vault path enforcement (L3 server-side gate).

All gbrain_put / gbrain_import wrappers must call assert_under_ocean(path)
before any write. This is defense layer 3 of 4 (see Phase 1 spec §7).
"""
import os

OCEAN_VAULT_ABSOLUTE_PATH: str = os.path.realpath(
    os.path.expanduser("~/Documents/Obsidian Vault/Ocean")
)


class PrivacyViolation(Exception):
    """Raised when a path is outside the Ocean vault."""


def assert_under_ocean(path: str) -> str:
    """
    Resolve symlinks and assert the path is strictly inside Ocean vault.
    Returns the resolved path on success. Raises PrivacyViolation otherwise.
    """
    resolved = os.path.realpath(path)
    if not (
        resolved.startswith(OCEAN_VAULT_ABSOLUTE_PATH + os.sep)
        or resolved == OCEAN_VAULT_ABSOLUTE_PATH
    ):
        raise PrivacyViolation(
            f"Path {path!r} (resolved: {resolved!r}) is outside Ocean vault "
            f"({OCEAN_VAULT_ABSOLUTE_PATH})"
        )
    return resolved
