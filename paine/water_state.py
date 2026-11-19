"""Conservative temporal filter. Totals are integer ticks at the configured precision."""

from collections import Counter
from datetime import date
import math


def convert_layout(state, decimals, omit):
    """Preserve physical volume across a precision change; discard unfinished votes."""
    if not state:
        return {}
    old = state.get('decimals',2)
    end = state.get('omit',1)
    if old+end != decimals+omit:
        raise ValueError('Water layout would reinterpret the cumulative register')
    if old==decimals:
        return dict(state, wheels=state.get('wheels',7))
    new = {k: state[k]*10**decimals//10**old for k in ('last','day_start') if k in state}
    if 'at' in state:
        new['at'] = state['at']
    return dict(new, wheels=state.get('wheels',7), decimals=decimals, omit=omit)


def decode(positions, digits=None):
    """Nearest centered digit; abstain in the middle of a mechanical carry."""
    if (len(positions) not in ((6,7) if digits is None else (digits,))
            or any(not 0 <= p < 100 for p in positions)):
        return None
    total = 0
    for p in positions[:6 if digits is None else digits]:
        if p % 10 in (4, 5, 6):
            return None
        total = total*10 + ((p+5)//10) % 10
    return total


def allowance(seconds, flow, decimals):
    return math.ceil(seconds*flow*10**decimals/60000)


def history_vote(row, alternatives, state, now, interval, flow, decimals):
    """Break a visual tie by the least possible forward distance, even with gaps."""
    last = state.get('last')
    age = now - state.get('at', now)
    if not alternatives or last is None or not 0 < age <= max(180, 3*interval):
        return row
    maximum = allowance(age, flow, decimals)
    checkpoint = str(last).zfill(len(row))
    if len(checkpoint) != len(row) or any(not 0 <= p < 100 for p in row):
        return row

    def distance(phases):
        # Find the first compatible digit string >= checkpoint. A rolling wheel
        # is a wildcard for comparison only; it remains unreadable in the vote.
        def match(i, tight, total):
            if i == len(phases):
                return total-last
            limit = int(checkpoint[i]) if tight else 0
            phase = phases[i]
            digits = range(limit, 10) if phase % 10 in (4, 5, 6) else (((phase+5)//10 % 10),)
            for digit in digits:
                if digit >= limit:
                    delta = match(i+1, tight and digit == limit, total*10+digit)
                    if delta is not None:
                        return delta
            return None
        return match(0, True, 0)

    delta = distance(row)
    options = [(delta, row)] if delta is not None and delta <= maximum else []
    for i, (phase, alts) in alternatives.items():
        if i >= len(row) or row[i] != phase:
            continue  # omitted wheel or existing visual correction
        for alt in alts:
            other = row.copy()
            other[i] = alt
            delta = distance(other)
            if delta is not None and delta <= maximum:
                options.append((delta, other))
    options.sort()
    # A one-tick floor avoids treating zero consumption as infinitely likely.
    if options and (len(options) == 1 or options[1][0] + 1 >= 3 * (options[0][0] + 1)):
        return options[0][1]
    return row


def caution_wheels(row, evidence, state, now, interval, flow, decimals):
    """Flag a wheel only if retaining its checkpoint digit strongly favors history."""
    if 'last' not in state:
        return []
    checkpoint = str(state['last']).zfill(len(row))
    flagged = []
    for i, (phase, digits) in evidence.items():
        if i >= len(row) or row[i] != phase or len(checkpoint) != len(row):
            continue
        digit = int(checkpoint[i])
        if digit in digits and history_vote(row, {i:(phase, [10*digit])}, state, now,
                                            interval, flow, decimals) != row:
            flagged.append(i)
    return flagged


def decide(state, rows, selected, now, interval, flow, digits=None, decimals=2, history_adjusted=False,
           warnings=None):
    """Agreement measures repeatability, not a calibrated probability of truth."""
    votes = Counter(v for row in rows if (v := decode(row,digits)) is not None)
    composite = decode(selected,digits)
    def needs_confirmation(value, width):
        return any(min(wheels) < width and decode(row[:width], width) == value
                   for row, wheels in (warnings or {}).items())
    def allowed(seconds):
        return allowance(seconds, flow, decimals)
    candidate, count = votes.most_common(1)[0] if votes else (None, 0)
    last, last_at = state.get('last'), state.get('at')
    pending, n = state.get('pending'), state.get('count', 0)
    age = now-last_at if last_at is not None else None
    maximum = allowed(max(0, age)) if age is not None else None
    event = dict(candidate_ticks=candidate, agreeing=count, aligned=len(rows),
                 selected_ticks=composite, previous_ticks=last, age_s=age,
                 abstaining=len(rows)-sum(votes.values()),
                 delta_ticks=candidate-last if candidate is not None and last is not None else None,
                 max_delta_ticks=maximum, votes=dict(votes.most_common(4)))
    new = {k: state[k] for k in ('last', 'at', 'day_start') if k in state}
    # Five original frames are a majority of the nine attempted shots. A
    # composite is not an independent vote and cannot veto a plausible majority.
    historical_majority = (count >= 5 and last is not None and age >= 0
                           and 0 <= candidate-last <= maximum)
    event['history_supported_majority'] = historical_majority
    if not historical_majority and (count < 6 or (count < 8 and composite != candidate)):
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
        if reason == 'flow_limit' and count >= 8 and composite == candidate:
            gap = now-state.get('alt_at', now)
            follows = ('alt' in state and 10 <= gap <= max(180, 3*interval)
                       and 0 <= candidate-state['alt'] <= allowed(gap))
            hits = state['alt_hits']+1 if follows else 1
            event.update(alternative_hits=hits, alternative_required=5, conflict=reason)
            if hits == 5:
                new.update(last=candidate, at=now)
                reason = 'baseline_corrected'
            else:
                new.update(alt=candidate, alt_at=now, alt_hits=hits)
    else:
        # A long gap can make even a badly misread digit pass the elapsed-time
        # flow limit. Fast-track at most one interval's consumption, and leave
        # an already pending trajectory to the corroboration rules below.
        caution = last is not None and candidate > last and needs_confirmation(candidate, len(selected))
        if caution:
            event['history_caution'] = True
        clear_increase = (not caution and not history_adjusted and historical_majority and pending is None and count >= 6
                          and len(votes) == 1 and composite == candidate
                          and 0 < candidate-last <= allowed(interval))
        required = 3 if last is None else (1 if candidate == last or clear_increase else 2)
        gap = now-state.get('pending_at', now)
        # Only distinct scheduled bursts count. Never confirm old pending data
        # after a sizeable gap/restart, or let a rejected read move the baseline.
        fresh = 10 <= gap <= max(180, 3*interval)
        # During flow the hundredths wheel may advance between every burst.
        # Require strong independent evidence on the new endpoint as well as
        # plausible deltas from both the checkpoint and the pending reading.
        advancing = (historical_majority and pending is not None and last < pending < candidate
                     and fresh and candidate-pending <= allowed(gap)
                     and count >= 7 and composite == candidate)
        if advancing:
            event.update(moving_from_ticks=pending, moving_gap_s=gap)
        n = n+1 if fresh and (candidate == pending or advancing) else 1
        event.update(confirmations=n, required=required)
        if n >= required:
            reason = 'confirmed'
            new.update(last=candidate, at=now)
        else:
            reason = 'bootstrap' if last is None else 'confirm_change'
            new.update(pending=candidate, count=n, pending_at=now)
    # A split on the litre wheel can still establish a lower bound. Only use
    # original frames, and never fast-track a large jump after a long outage.
    if reason == 'weak_burst' and not history_adjusted and digits is not None and last is not None and age > 0:
        for drop in range(1, min(decimals, digits-1)+1):
            prefixes = Counter(p for row in rows if len(row) == digits
                               and (p := decode(row[:-drop], digits-drop)) is not None)
            if not prefixes:
                continue
            prefix, support = prefixes.most_common(1)[0]
            if support < 6:
                continue
            step = 10**drop
            floor = prefix * step
            if (any(v // step == prefix for v in votes)
                    and 0 < floor-last <= allowed(min(age, interval))):
                event.update(fallback_resolution_liters=step * 10**(3-decimals), fallback_agreeing=support,
                             fallback_ticks=floor)
                caution = needs_confirmation(prefix, digits-drop)
                gap = now-state.get('pending_at', now)
                corroborated = (pending is not None and 10 <= gap <= max(180, 3*interval)
                                and 0 <= floor-pending <= allowed(gap))
                if caution:
                    event.update(history_caution=True, confirmations=2 if corroborated else 1, required=2)
                if caution and not corroborated:
                    new.update(pending=floor, pending_at=now, count=1)
                    reason = 'confirm_change'
                else:
                    new = dict(last=floor, at=now)
                    if 'day_start' in state:
                        new['day_start'] = state['day_start']
                    candidate, reason = floor, 'confirmed'
            break  # Never discard agreed digits to make an implausible jump fit.
    if reason in ('confirmed', 'baseline_corrected'):
        # `at` always belongs to the last accepted reading, even across rejects.
        if ('day_start' not in state or date.fromtimestamp(now) != date.fromtimestamp(last_at)
                or candidate < state['day_start']):
            new['day_start'] = candidate
    return new, dict(event, decision=reason)
