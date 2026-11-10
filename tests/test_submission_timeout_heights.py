"""Timeout-height boundaries for payload submissions.

The boundary must match the chain's inclusive submission windows
(`x/emissions/keeper/window_boundaries.go`):

- worker:        [nonce, nonce + worker_submission_window]
- reputer nonce: [nonce + ground_truth_lag + extra_lag, + epoch_length], with
                 extra_lag = (-ground_truth_lag) mod epoch_length
"""
from allora_sdk.worker.utils import (
    reputer_submission_timeout_height,
    worker_submission_timeout_height,
)


def test_worker_timeout_is_the_window_end():
    assert worker_submission_timeout_height(1000, 9) == 1009


def test_worker_timeout_is_none_when_window_unknown():
    assert worker_submission_timeout_height(1000, 0) is None


def test_reputer_timeout_rounds_the_lag_onto_the_epoch_grid():
    # lag 50, epoch 36 -> extra = 36 - (50 % 36) = 22; start = 1072; end = 1108
    assert reputer_submission_timeout_height(1000, 50, 36) == 1108


def test_reputer_timeout_has_no_extra_lag_on_whole_multiples():
    # lag 72 = 2 * epoch 36 -> extra = 0; start = 1072; end = 1108
    assert reputer_submission_timeout_height(1000, 72, 36) == 1108


def test_reputer_timeout_is_none_when_topic_values_unknown():
    assert reputer_submission_timeout_height(1000, 0, 36) is None
    assert reputer_submission_timeout_height(1000, 50, 0) is None
