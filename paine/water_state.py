"""Conservative temporal filter. Totals are integer 10-litre ticks, not floats."""

from collections import Counter
import math


def decode(positions):
    """Nearest centered digit; abstain in the middle of a mechanical carry."""
    if len(positions) != 6 or any(not 0 <= p < 100 for p in positions):
        return None
    total = 0
    for p in positions:
        if p % 10 in (4, 5, 6):
            return None
        total = total*10 + ((p+5)//10) % 10
    return total


def decide(state, rows, selected, now, interval, flow):
    """Agreement measures repeatability, not a calibrated probability of truth."""
    votes = Counter(v for row in rows if (v := decode(row)) is not None)
    candidate, count = votes.most_common(1)[0] if votes else (None, 0)
    last, last_at = state.get('last'), state.get('at')
    pending, n = state.get('pending'), state.get('count', 0)
    age = now-last_at if last_at is not None else None
    maximum = math.ceil(max(0, age)*flow/600) if age is not None else None
    event = dict(candidate_ticks=candidate, agreeing=count, aligned=len(rows),
                 selected_ticks=decode(selected), previous_ticks=last, age_s=age,
                 abstaining=len(rows)-sum(votes.values()),
                 delta_ticks=candidate-last if candidate is not None and last is not None else None,
                 max_delta_ticks=maximum, votes=dict(votes.most_common(4)))
    new = {k: state[k] for k in ('last', 'at') if k in state}
    if count < 6 or (count < 8 and decode(selected) != candidate):
        reason = 'weak_burst'
        # Inconclusive evidence needn't restart commissioning if it still
        # favors the pending value. It never adds a confirmation or freshness.
        if pending is not None and candidate == pending and 0 <= now-state['pending_at'] <= 3*interval:
            new.update(pending=pending, count=n, pending_at=state['pending_at'])
    elif age is not None and age < 0:
        reason = 'clock_reversed'
    elif last is not None and (candidate < last or candidate-last > maximum):
        reason = 'decrease' if candidate < last else 'flow_limit'
        # Track an alternative trajectory separately; never refresh the old
        # timestamp with rejected observations. Demand more evidence than for
        # commissioning, since correction can affect HA cumulative statistics.
        if count >= 8 and decode(selected) == candidate:
            gap = now-state.get('alt_at', now)
            follows = ('alt' in state and 10 <= gap <= max(180, 3*interval)
                       and 0 <= candidate-state['alt'] <= math.ceil(gap*flow/600))
            hits = state['alt_hits']+1 if follows else 1
            event.update(alternative_hits=hits, alternative_required=5, conflict=reason)
            if hits == 5:
                new.update(last=candidate, at=now)
                reason = 'baseline_corrected'
            else:
                new.update(alt=candidate, alt_at=now, alt_hits=hits)
    else:
        required = 3 if last is None else (2 if candidate != last else 1)
        gap = now-state.get('pending_at', now)
        # Only distinct scheduled bursts count. Never confirm old pending data
        # after a sizeable gap/restart, or let a rejected read move the baseline.
        n = n+1 if candidate == pending and 10 <= gap <= max(180, 3*interval) else 1
        event.update(confirmations=n, required=required)
        if n >= required:
            reason = 'confirmed'
            new.update(last=candidate, at=now)
        else:
            reason = 'bootstrap' if last is None else 'confirm_change'
            new.update(pending=candidate, count=n, pending_at=now)
    return new, dict(event, decision=reason)
