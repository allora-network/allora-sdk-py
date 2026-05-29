# CZAR (Composite Zero-Agnostic Return Loss) loss function as described by Joel in RES-1292

import numpy as np

def derivative(x):
    # Cauchy/Lorentz function
    return 1.0 / (1.0 + x**2)

def antiderivative(x):
    # Integral of derivative function
    return np.arctan(x)

def double_derivative(x):
    # Derivative of Cauchy/Lorentz function
    return 2.0 * abs(x) / (1.0 + x**2)**2

def eps_effective(eps, delta):
    # Rescale epsilon so that 1 - loss(z_true, 0) / loss(0, epsilon) crosses zero at epsilon
    if abs(delta) == 0:
        return np.arctan(eps)

    A = (1 + delta**2) * (antiderivative(eps + delta) - antiderivative(delta))
    beta = delta / (1 + delta**2)  # coefficient on eps_eff^2 in loss(0, eps_eff, 1)

    # Solve beta*x^2 + x - A = 0 for positive x
    return (-1 + np.sqrt(1 + 4 * beta * A)) / (2 * beta)

def softplus(x):
    # Smooth hinge function centred at 0
    #   y ~= x for x > 0
    #   y = 0 for x -> -inf
    return max(x, 0.0) + np.log1p(np.exp(-abs(x)))

def norm_smooth(z_true, eps, delta, tau):
    # Smoothing normalisation
    # Minimum value of the normalisation at z_true set by the limit that loss(z_true,0)
    # does not decrease as z_true increases. Simplified from: 1 - loss(z_true, 0) / loss(0, epsilon).
    # Generally norm < 0 for |z_true| > eps (which would result in negative losses),
    # so need to transition to 0 for large |z_true|
    a = abs(z_true)
    d2p1 = delta**2 + 1
    num = d2p1 * (antiderivative(a + delta) - antiderivative(delta))
    denom = eps + delta / d2p1 * eps**2
    norm_min = 1.0 - num / denom

    if tau <= 0:
        # Hard transition
        return max(norm_min, 0.0)

    # Smooth transition when norm drops below zero
    # Scale tau_eff by |norm_inf| so the asymptote is invariant across eps, delta
    # Asymptotic value of norm_min as |z_true| -> inf
    num_inf  = d2p1 * (0.5*np.pi - antiderivative(delta))
    norm_inf = 1.0 - num_inf / denom
    tau_eff = abs(tau) * abs(norm_inf)
    return softplus(norm_min / tau_eff) / softplus(1 / tau_eff)

def czar_loss(y_true, y_pred, std, mean=0, alpha=0.01, epsilon=1, tau=0.05):
    """
    Composite Zero-Agnostic Return Loss

    Asymmetric, piecewise function that is
        * Linear (alpha=0) or quadratic (alpha>0) when y_pred has opposite sign to y_true
        * Linear (alpha=0) or quadratic (alpha>0) when |y_pred| > |y_true|, with a decreasing gradient as |z_true| increases
        * Arctangent transition from 0 < |y_pred| < |y_true|

    Args:
        y_true: True returns
        y_pred: Predicted returns
        std: Standard deviation of true returns
        mean: Mean of true returns
        alpha: MSE term constant (alpha=0 is linear only, alpha=1 is maximum gradient)
        epsilon: Loss softening scale, in units of standard devation. Optimum is eps~1
        tau: Scaling for softening hinge function
    Returns:
        Value of loss
    """

    if alpha < 0 or alpha > 1:
        raise ValueError(f'[czar_loss] alpha must be between 0 and 1, got {alpha}')

    # Z-score transformation
    z_true = (y_true - mean) / std
    z_pred = (y_pred - mean) / std

    # Preserve directionality (losses for +z_true are mirrored for -z_true)
    # When z_true = 0 have a symmetric function
    s = np.sign(z_true) if z_true != 0 else 1
    s_pred = np.sign(z_pred) if z_pred != 0 else 1
    a = abs(z_true)
    u = s * z_pred

    # Apply horizontal shift to arctan function for smooth change in gradient
    # Alpha should be between 0 (linear) and 1 (maximum gradient of Cauchy/Lorentz function).
    # Factor of 1/sqrt(3) shifts to the peak of the hessian function
    delta = alpha / np.sqrt(3)
    d2p1 = delta**2 + 1

    d_true = z_true + s * delta
    d_pred = z_pred + s_pred * delta

    # Base loss
    if u <= 0:
        # Region 1: opposite sign (u <= 0): grad = -s + MSE term
        # Constant so that the middle branch hits zero at z_pred = z_obs
        C = s * d2p1 * (antiderivative(d_true) - antiderivative(s * delta))
        loss = 0.5 * d2p1 * double_derivative(delta) * z_pred**2 - s * z_pred + C

    elif u <= a:
        # Region 2, arctan: same sign, before threshold (0 < u <= a): grad = -s * derivative(z_pred)
        # antiderivative(z_true) term so that loss = 0 at z_pred = z_true
        loss = s * d2p1 * (antiderivative(d_true) - antiderivative(d_pred))

    else:
        # Region 3, linear: past threshold (u > a): grad = s * derivative(z_true) + MSE term
        # abs(Gradient) decreases as abs(z_true) increases
        dz = z_pred - z_true

        # Hessian values for regions 1 and 3
        h1 = d2p1 * double_derivative(delta)
        h3 = d2p1 * double_derivative(d_true)

        loss = 0.5 * min(h3, h1) * dz**2 + s * d2p1 * derivative(d_true) * dz

    # Softening term (addition to base loss), increases minimum loss at z_true=0, and
    # the term decreases as abs(z_true) increases
    if epsilon > 0:
        # Rescale epsilon so that 1 - loss(z_true, 0) / loss(0, epsilon) crosses zero at epsilon
        eps_eff = eps_effective(epsilon, delta)

        # Define softening as the base loss at z_pred=epsilon when z_true=0
        softening_0 = czar_loss(0, eps_eff, 1., epsilon=0, alpha=alpha)

        # Decrease softening normalisation with increasing |z_true|, aiming to be as
        # close as possible to loss(y_true, 0) = const
        norm = norm_smooth(z_true, eps_eff, delta, tau)
        loss_soft = norm * softening_0
    else:
        loss_soft = 0

    return loss + loss_soft
