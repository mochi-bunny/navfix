"""Confidence that a skill's true success rate is >= target.

Used only by the MOCK cell service. In live mode these numbers come from the
real cell service and the dashboard never computes them.

Posterior Beta(a, b) with a = successes + 1, b = failures + 1 (uniform prior).
For integer a, b:  P(p >= x) = P(Binomial(a + b - 1, x) <= a - 1).
"""
from math import comb


def p_rate_at_least(successes, trials, target=0.8):
    a = successes + 1
    b = (trials - successes) + 1
    n = a + b - 1
    return sum(comb(n, k) * target ** k * (1 - target) ** (n - k) for k in range(a))
