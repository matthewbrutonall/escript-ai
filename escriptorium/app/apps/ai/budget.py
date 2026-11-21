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


def estimate_job_cost(config, parts, per_crop=6, *, segmentation=False):
    from django.apps import apps
    Line = apps.get_model('core', 'Line')
    pks = [part.pk for part in parts]
    from django.db.models import Count
    counts = dict(Line.objects.filter(document_part_id__in=pks).values_list('document_part_id').annotate(n=Count('pk')))
    return estimate_cost_from_counts(config, [counts.get(pk, 0) for pk in pks], per_crop, segmentation=segmentation)


def estimate_cost_from_counts(config, line_counts, per_crop=6, *, segmentation=False):
    rates = {
        'gemini': (0.30, 2.50), 'anthropic': (3.00, 15.00),
        'openai': (2.00, 12.00), 'azure': (2.00, 12.00),
        'mistral': (0.15, 0.15), 'local': (0.0, 0.0),
    }
    input_rate, output_rate = rates.get(config.provider, (0.0, 0.0))
    if input_rate == output_rate == 0:
        return 0.0
    edge = max(1, int(config.max_edge_px or 1024))
    input_tokens = max(258, ((edge + 767) // 768) ** 2 * 258) + 1024
    output_tokens = 256
    calls = 0
    for count in line_counts:
        groups = (count + max(1, per_crop) - 1) // max(1, per_crop)
        calls += 1 if segmentation else groups
    return calls * (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
