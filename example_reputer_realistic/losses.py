import math
from czar import czar_loss as czar_loss_orig

### LOSS FUNCTIONS

def mse_loss(x: float, y: float) -> float:
    return (x-y)**2

def czar_loss(inf: float, gt: (float, float)) -> float:
    (ground_truth, std) = gt
    return float(czar_loss_orig(ground_truth, inf, std))

def _power_tanh(x: float, alpha: float, beta: float) -> float:
    try:
        exponent = (1.0 - alpha) / beta
        return x / (1.0 + abs(x) ** beta) ** exponent
    except OverflowError:
        return math.copysign(abs(x) ** alpha, x)

def zptae_loss(inference: float, gt: (float, float), alpha = 0.25, beta = 2.0, gamma = 4.0, mse_norm = 0.01) -> float:
    ground_truth = gt[0]
    std = gt[1]
    mean = 0.0

    z_true = (ground_truth - mean) / stdcar
    z_pred = (inference - mean) / std

    pt_true = _power_tanh(z_true, alpha, beta)
    pt_pred = _power_tanh(z_pred, alpha, beta)

    main_term = abs(pt_true - pt_pred)
    mse_term = (mse_norm * abs(z_pred - z_true)) ** gamma

    return main_term + mse_term

LOSS_FUNCTIONS = {
    'mse' : mse_loss,
    'czar': czar_loss,
    'zptae': zptae_loss
}
