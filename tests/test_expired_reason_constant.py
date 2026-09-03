"""The expiration reason string is a contract between the syncer and the SPA.

Expiration is not modelled as its own status or column, so `isExpiredRequest`
in `src/helpers.tsx` matches the backend's reason text verbatim to decide
whether to offer a "reopen" button. Rewording one side without the other
silently removes that button, with no other test failing. This pins the pair.
"""

from pathlib import Path

from api.syncer import EXPIRED_REQUEST_REASON

_HELPERS = Path(__file__).resolve().parent.parent / "src" / "helpers.tsx"


def test_frontend_mirrors_the_expired_reason() -> None:
    source = _HELPERS.read_text(encoding="utf-8")
    assert f"'{EXPIRED_REQUEST_REASON}'" in source, (
        f"{_HELPERS} must contain the exact reason string {EXPIRED_REQUEST_REASON!r}. "
        "If you reworded api.syncer.EXPIRED_REQUEST_REASON, update "
        "EXPIRED_REQUEST_REASON in src/helpers.tsx to match."
    )
