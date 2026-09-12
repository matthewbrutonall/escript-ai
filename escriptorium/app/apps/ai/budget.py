"""Monthly AI spend caps (§11 / §12).

None or negative cap = unlimited. Cap 0 = blocked, even when est_cost is 0
(jobs often enqueue with no estimate).
"""


def budget_allows(spent, cap, est_cost=0.0):
    if cap is None or cap < 0:
        return True
    if float(cap) == 0:
        return False
    return (float(spent or 0) + float(est_cost or 0)) <= float(cap)


def remaining(spent, cap):
    if cap is None or cap < 0:
        return None
    return max(0.0, float(cap) - float(spent or 0))


def month_start(now):
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
