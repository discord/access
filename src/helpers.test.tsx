import dayjs from 'dayjs';
import {describe, expect, it} from 'vitest';

import {EXPIRED_REQUEST_REASON, isExpiredRequest, reconstructRequestedUntil} from './helpers';

describe('isExpiredRequest', () => {
  it('matches a request closed by the expiration sweep', () => {
    expect(isExpiredRequest({status: 'REJECTED', resolver: null, resolution_reason: EXPIRED_REQUEST_REASON})).toBe(
      true,
    );
  });

  it('does not match a human rejection, even with no reason given', () => {
    expect(isExpiredRequest({status: 'REJECTED', resolver: {id: 'u1'}, resolution_reason: 'no thanks'})).toBe(false);
    expect(isExpiredRequest({status: 'REJECTED', resolver: {id: 'u1'}, resolution_reason: ''})).toBe(false);
  });

  it('does not match the other system closures, which also have a null resolver', () => {
    for (const reason of [
      'Closed because the requestor was deleted',
      'Closed because the requested group was deleted',
      'Closed because the requested group is no longer managed by Access',
      'Denied by conditional access policy',
    ]) {
      expect(isExpiredRequest({status: 'REJECTED', resolver: null, resolution_reason: reason})).toBe(false);
    }
  });

  it('does not match a pending or approved request', () => {
    expect(isExpiredRequest({status: 'PENDING', resolver: null, resolution_reason: EXPIRED_REQUEST_REASON})).toBe(
      false,
    );
    expect(isExpiredRequest({status: 'APPROVED', resolver: null, resolution_reason: EXPIRED_REQUEST_REASON})).toBe(
      false,
    );
  });

  it('tolerates missing fields rather than throwing', () => {
    expect(isExpiredRequest({})).toBe(false);
  });
});

const LABELS: Record<string, string> = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  '2592000': '30 Days',
  '7776000': '90 Days',
  indefinite: 'Indefinite',
  custom: 'Custom',
};

describe('reconstructRequestedUntil', () => {
  it('reports indefinite when there was no end date', () => {
    expect(reconstructRequestedUntil({createdAt: '2026-01-01T00:00:00', endingAt: null, untilLabels: LABELS})).toEqual({
      until: 'indefinite',
      deltaSeconds: null,
    });
  });

  it('round-trips a duration that matches an option exactly', () => {
    // 90 days == 7776000 seconds.
    const createdAt = '2026-01-01T00:00:00';
    const endingAt = dayjs(createdAt).add(7776000, 'second').toISOString();
    expect(reconstructRequestedUntil({createdAt, endingAt, untilLabels: LABELS})).toEqual({
      until: '7776000',
      deltaSeconds: 7776000,
    });
  });

  it('absorbs sub-second drift via the 100s rounding', () => {
    const createdAt = '2026-01-01T00:00:00';
    const endingAt = dayjs(createdAt).add(7776043, 'second').toISOString();
    expect(reconstructRequestedUntil({createdAt, endingAt, untilLabels: LABELS})).toEqual({
      until: '7776000',
      deltaSeconds: 7776000,
    });
  });

  it('re-offers a non-matching duration as a custom date based from today', () => {
    const createdAt = '2026-01-01T00:00:00';
    const fiftyDays = 50 * 24 * 60 * 60;
    const endingAt = dayjs(createdAt).add(fiftyDays, 'second').toISOString();

    const result = reconstructRequestedUntil({createdAt, endingAt, untilLabels: LABELS});

    expect(result.until).toBe('custom');
    expect(result.deltaSeconds).toBe(fiftyDays);
    // Re-based from now, not the original absolute date, which is in the past.
    expect(result.customUntil!.diff(dayjs(), 'day')).toBe(50);
  });

  it('clamps to the largest allowed option when a time limit is now tighter', () => {
    const createdAt = '2026-01-01T00:00:00';
    const endingAt = dayjs(createdAt).add(7776000, 'second').toISOString();

    // Tag limit is now 30 days; the original 90-day ask is no longer offerable.
    // deltaSeconds stays the raw unclamped ask.
    expect(reconstructRequestedUntil({createdAt, endingAt, untilLabels: LABELS, timeLimit: 2592000})).toEqual({
      until: '2592000',
      deltaSeconds: 7776000,
    });
  });

  it('clamps indefinite to the largest allowed option under a time limit', () => {
    expect(
      reconstructRequestedUntil({
        createdAt: '2026-01-01T00:00:00',
        endingAt: null,
        untilLabels: LABELS,
        timeLimit: 1209600,
      }),
    ).toEqual({until: '1209600', deltaSeconds: null});
  });

  it('does not clamp when no time limit is given', () => {
    const createdAt = '2026-01-01T00:00:00';
    const endingAt = dayjs(createdAt).add(7776000, 'second').toISOString();
    expect(reconstructRequestedUntil({createdAt, endingAt, untilLabels: LABELS})).toEqual({
      until: '7776000',
      deltaSeconds: 7776000,
    });
  });
});
