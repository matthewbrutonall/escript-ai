"""Monthly AI spend caps (§11 / §12). Cap None or <= 0 means unlimited."""


def budget_allows(spent, cap, est_cost=0.0):
    if cap is None or cap < 0:
        return True
    return (float(spent or 0) + float(est_cost or 0)) <= float(cap)


def remaining(spent, cap):
    if cap is None or cap < 0:
        return None
    return max(0.0, float(cap) - float(spent or 0))


def month_start(now):
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
