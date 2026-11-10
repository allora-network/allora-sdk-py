"""`is_block_not_ready` classifies the chain's transient "height not produced yet"
response.

cosmos-sdk raises it as ``INVALID_ARGUMENT`` with a fixed message and no error
details; the status alone is shared with other query errors, so the message is
the discriminator. Only the distinctive core of the wording is matched, so a
rephrased prefix still classifies.
"""

from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.worker.utils import is_block_not_ready

BLOCK_NOT_READY = GRPCError(
    Status.INVALID_ARGUMENT,
    "requested block height is bigger then the chain length",
    None,
)


def test_matches_the_chain_response():
    assert is_block_not_ready(BLOCK_NOT_READY)


def test_matches_a_rephrased_prefix():
    rephrased = GRPCError(
        Status.INVALID_ARGUMENT,
        "height 123: bigger then the chain length",
        None,
    )
    assert is_block_not_ready(rephrased)


def test_other_invalid_argument_errors_do_not_match():
    other = GRPCError(Status.INVALID_ARGUMENT, "some other invalid argument", None)
    assert not is_block_not_ready(other)


def test_other_statuses_do_not_match():
    other = GRPCError(
        Status.NOT_FOUND,
        "requested block height is bigger then the chain length",
        None,
    )
    assert not is_block_not_ready(other)


def test_non_grpc_errors_do_not_match():
    assert not is_block_not_ready(ValueError("bigger then the chain length"))
