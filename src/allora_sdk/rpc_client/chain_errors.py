"""Named ABCI codespaces and codes the SDK branches on.

Numbers mirror ``allora-chain`` ``x/emissions/types/errors.go`` and the
cosmos-sdk ``sdk`` codespace. Keep this module as the single place that
names them so call sites do not scatter magic integers.
"""

from __future__ import annotations

import re
from typing import Optional

# --- codespaces ----------------------------------------------------------------

EMISSIONS_CODESPACE = "emissions"
SDK_CODESPACE = "sdk"

# --- sdk codes -----------------------------------------------------------------

# ErrTxTimeoutHeight — timeout_height passed before the tx could land.
SDK_ERR_TX_TIMEOUT_HEIGHT = 30

# --- emissions codes (subset the SDK inspects) ---------------------------------

# ErrReputerNonceWindowNotAvailable — also treated as "already done" for
# worker/reputer submit classification when the window is gone.
EMISSIONS_ERR_REPUTER_NONCE_WINDOW_NOT_AVAILABLE = 68

# ErrUnfulfilledNonceNotFound — nonce already fulfilled / not open.
EMISSIONS_ERR_UNFULFILLED_NONCE_NOT_FOUND = 75

# ErrCantUpdateEmaMoreThanOncePerWindow — duplicate submission for the window.
EMISSIONS_ERR_CANT_UPDATE_EMA_MORE_THAN_ONCE_PER_WINDOW = 78

# ErrNotPermittedToSubmitWorkerPayload — inference / worker gate.
EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_WORKER_PAYLOAD = 83

# ErrNotPermittedToSubmitForecasterPayload — forecaster gate (v0.18+).
EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_FORECASTER_PAYLOAD = 104

# --- named sets for worker / submit classification -----------------------------

# Codes that mean "this nonce is done; do not retry".
ALREADY_SUBMITTED_CODES: frozenset[int] = frozenset(
    {
        EMISSIONS_ERR_REPUTER_NONCE_WINDOW_NOT_AVAILABLE,
        EMISSIONS_ERR_UNFULFILLED_NONCE_NOT_FOUND,
        EMISSIONS_ERR_CANT_UPDATE_EMA_MORE_THAN_ONCE_PER_WINDOW,
    }
)

# Codes that mean "signer lacks permission for a present payload part".
PERMISSION_DENIED_CODES: frozenset[int] = frozenset(
    {
        EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_WORKER_PAYLOAD,
        EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_FORECASTER_PAYLOAD,
    }
)

# cosmos-sdk / tendermint often emit: "codespace emissions code 83"
_ABCI_CODESPACE_CODE_RE = re.compile(
    r"codespace\s*[:=]?\s*([A-Za-z0-9_./-]+)\s+code\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)
# Compact form sometimes seen in logs: "emissions:83"
_ABCI_COMPACT_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_./-]{1,32}):(\d{1,5})\b",
)


def is_already_submitted_code(code: int) -> bool:
    """True when ``code`` means the nonce should not be retried."""
    return code in ALREADY_SUBMITTED_CODES


def is_permission_denied_code(code: int) -> bool:
    """True when ``code`` is an emissions permission rejection."""
    return code in PERMISSION_DENIED_CODES


def is_tx_timeout_height(codespace: str, code: int) -> bool:
    """True for sdk ErrTxTimeoutHeight."""
    return codespace == SDK_CODESPACE and code == SDK_ERR_TX_TIMEOUT_HEIGHT


def parse_abci_codespace_code(message: str) -> Optional[tuple[str, int]]:
    """Extract ``(codespace, code)`` from a simulation / ABCI error message.

    Returns None when the message does not carry a recognizable ABCI pair.
    Prefers the explicit ``codespace … code …`` form over the compact
    ``codespace:code`` form.
    """
    if not message:
        return None

    match = _ABCI_CODESPACE_CODE_RE.search(message)
    if match:
        return match.group(1), int(match.group(2))

    # Prefer a trailing compact match that looks like a module codespace
    # (emissions, sdk, bank, …) rather than the first ``word:digits`` pair.
    for match in _ABCI_COMPACT_RE.finditer(message):
        codespace = match.group(1)
        if codespace.lower() in {EMISSIONS_CODESPACE, SDK_CODESPACE, "bank", "gov", "staking"}:
            return codespace, int(match.group(2))

    return None
