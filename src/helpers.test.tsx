import {describe, expect, it} from 'vitest';

import {EXPIRED_REQUEST_REASON, isExpiredRequest} from './helpers';

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
