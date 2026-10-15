"""Supplied sample summaries: validate, pool with counts, retain units and provenance."""
from decimal import Decimal, localcontext

from .fhir_quality_pipeline import number, serial

ROLES = ('value_mean', 'value_sd', 'value_min', 'value_max')


def supplied_statistics(values, quantity):
    present = {key: values.get(key) for key in ROLES if values.get(key) not in (None, '')}
    if not (set(present) & {"value_mean", "value_sd"}):
        return {}
    parsed = {key: number(value) for key, value in present.items()}
    if any(value is None for value in parsed.values()):
        raise ValueError('Aggregate statistics must be finite numbers')
    unit = quantity.get('code') or quantity.get('unit') or ''
    for key in present:
        declared = values.get(key + '_unit')
        if declared and declared != unit:
            raise ValueError('Aggregate statistic units must match the declared measurement unit; normalize the export first')
    low, high, mean, sd = (parsed.get(k) for k in ('value_min', 'value_max', 'value_mean', 'value_sd'))
    if sd is not None and sd < 0:
        raise ValueError('Sample SD must be nonnegative')
    if low is not None and high is not None and low > high:
        raise ValueError('Aggregate minimum exceeds maximum')
    if mean is not None and (low is not None and mean < low or high is not None and mean > high):
        raise ValueError('Aggregate mean lies outside supplied minimum/maximum')
    return {key.removeprefix('value_'): serial(value) for key, value in parsed.items()}


def pool_summaries(rows):
    """Pool only disjoint groups declared by the operator; SD uses n-1 convention."""
    result = {key: None for key in ('mean', 'sd', 'se', 'median', 'q1', 'q3', 'iqr')}
    if len(rows) == 1:
        result.update({key: rows[0].get(key) for key in ('mean', 'sd')})
        n = rows[0].get('n')
        if n and n < 2 and result['sd'] is not None:
            raise ValueError('Sample SD requires count >= 2')
        if n and result['sd'] is not None:
            result['se'] = serial(Decimal(result['sd']) / Decimal(n).sqrt())
        return result
    if not rows or not all(r.get('n') and r.get('mean') is not None for r in rows):
        return result
    with localcontext() as ctx:
        ctx.prec = 38
        n = sum(r['n'] for r in rows)
        mean = sum(Decimal(r['mean']) * r['n'] for r in rows) / n
        result['mean'] = serial(mean)
        if all(r.get('sd') is not None or r['n'] == 1 for r in rows) and n > 1:
            if any(r['n'] == 1 and r.get('sd') is not None for r in rows):
                raise ValueError('Sample SD requires count >= 2')
            ss = sum((r['n'] - 1) * Decimal(r.get('sd') or '0') ** 2 + r['n'] * (Decimal(r['mean']) - mean) ** 2 for r in rows)
            sd = (ss / (n-1)).sqrt()
            result.update(sd=serial(sd), se=serial(sd / Decimal(n).sqrt()))
    return result


def quantities(row, factor=None, target=None):
    unit = target if factor is not None else row.get('unit_code') or row.get('unit', '')
    result = {}
    for key in ('mean', 'sd', 'min', 'max', 'se', 'median', 'q1', 'q3', 'iqr'):
        value = number(row.get('value_' + key))
        if value is not None:
            result[key] = {'value': serial(value * factor if factor is not None else value), 'unit': unit,
                           'system': row.get('unit_system', '')}
    return result
