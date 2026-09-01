import * as React from 'react';

import {useQueries} from '@tanstack/react-query';

import {effectiveConstraintsQuery} from './api/apiComponents';
import type {EffectiveConstraintDetail} from './api/apiSchemas';

// Reading the constraints that apply to a group, a set of groups, or a set of
// tags.
//
// The coalescing itself — which tags are enabled, which reach a role through
// its associations, and the minimum or logical OR across them — lives in
// `api/models/tag.py` and is served by `GET /api/constraints/effective`. These
// readers only look a value up in what the API already resolved. Anything here
// resembling a min or an OR over *tags* would be re-introducing the
// duplication that endpoint exists to remove.

const TIME_LIMIT_KEYS = {member: 'member_time_limit', owner: 'owner_time_limit'} as const;
const REQUIRE_REASON_KEYS = {member: 'require_member_reason', owner: 'require_owner_reason'} as const;
const SELF_ADD_KEYS = {member: 'disallow_self_add_membership', owner: 'disallow_self_add_ownership'} as const;

// Mirrors `_EFFECTIVE_CONSTRAINTS_MAX_IDS` in `api/schemas/requests_schemas.py`.
// A selection larger than this is split across requests rather than rejected:
// a role can be associated with more groups than one request may name, and the
// dialog still has to answer for every row.
const MAX_IDS_PER_REQUEST = 200;

const SECONDS_PER_DAY = 86400;

/**
 * Render a time limit the way users think about it: in days.
 *
 * Sub-day limits are legal -- the constraint validator only requires a
 * positive integer -- and rounding one to the nearest day would print
 * "0 days", which reads as no access at all. Values are not guaranteed to
 * divide evenly either, and an unrounded quotient prints a falsely precise
 * fraction.
 */
export function timeLimitLabel(seconds: number): string {
  if (seconds < SECONDS_PER_DAY) {
    return '<1 day';
  }
  const days = Math.round(seconds / SECONDS_PER_DAY);
  return `${days} ${days === 1 ? 'day' : 'days'}`;
}

export type Constraints = EffectiveConstraintDetail[] | undefined | null;

function valueOf(constraints: Constraints, key: string): number | boolean | undefined {
  return constraints?.find((entry) => entry.constraint === key)?.value;
}

function side<T>(keys: {member: T; owner: T}, isOwner: boolean): T {
  return isOwner ? keys.owner : keys.member;
}

// Seconds, or null when no limit applies. Compared against `undefined` rather
// than tested for truthiness: a `0`-second limit is a real constraint, and
// treating it as absent would offer an unbounded duration.
export function effectiveTimeLimit(constraints: Constraints, isOwner: boolean): number | null {
  const value = valueOf(constraints, side(TIME_LIMIT_KEYS, isOwner));
  return typeof value === 'number' ? value : null;
}

export function isReasonRequired(constraints: Constraints, isOwner: boolean): boolean {
  return valueOf(constraints, side(REQUIRE_REASON_KEYS, isOwner)) === true;
}

export function isSelfAddDisallowed(constraints: Constraints, isOwner: boolean): boolean {
  return valueOf(constraints, side(SELF_ADD_KEYS, isOwner)) === true;
}

export interface ApprovalUntilInput {
  /** The `until` option matching what the requester asked for. */
  requestedUntil: string;
  /** The longest option still on offer under the limit, if any. */
  requestedUntilAdjusted: string | undefined;
  /** The limit in force, or null when none applies or none is known yet. */
  timeLimit: number | null;
  /** Whether what was requested already fits inside the limit. */
  autofillUntil: boolean;
}

/**
 * The duration an approval form should start on, or null to leave it alone.
 *
 * An approval page has three inputs arriving on different renders -- the
 * request, the constraints, and the option list derived from them -- and React
 * Hook Form takes its `defaultValues` from the first render, before any of
 * them. So the starting value has to be written after the fact, and this is
 * the decision behind that write, kept out of the dialogs because neither can
 * be rendered under vitest.
 *
 * Two rules, in order:
 *
 * - A limit narrower than what was asked for moves the approver to the longest
 *   duration still on offer, so the field never holds an option the list no
 *   longer contains. A limit leaving nothing on offer returns null: there is
 *   no valid duration to select, and inventing one would be worse than leaving
 *   the field where it is.
 * - Otherwise the requested duration is allowed and is the default. Returning
 *   null here instead would leave the form on its pre-request snapshot, which
 *   reads as indefinite -- an approver would grant unbounded access by
 *   accepting a default that looks like the request.
 */
export function approvalUntilDefault({
  requestedUntil,
  requestedUntilAdjusted,
  timeLimit,
  autofillUntil,
}: ApprovalUntilInput): string | null {
  if (timeLimit != null && !autofillUntil) {
    return requestedUntilAdjusted ?? null;
  }
  return requestedUntil;
}

// Stable, deduplicated, sorted ids so the query key does not change when the
// same selection arrives in a different order. That makes the result cacheable
// and, in the bulk dialogs where the selection changes as rows are toggled,
// keeps a slow response for an older selection from landing after a newer one.
function stableIds(ids: (string | undefined | null)[]): string[] {
  return Array.from(new Set(ids.filter((id): id is string => !!id))).sort();
}

function chunked(ids: string[]): string[][] {
  if (ids.length === 0) return [];
  const out: string[][] = [];
  for (let i = 0; i < ids.length; i += MAX_IDS_PER_REQUEST) {
    out.push(ids.slice(i, i + MAX_IDS_PER_REQUEST));
  }
  return out;
}

// Combine the per-chunk roll-ups a split request came back with.
//
// This is NOT the tag coalescing the module header warns about: it folds the
// API's own answers across batches of one logical question, and which rule to
// apply follows from the value's type rather than from any knowledge of what
// the constraint means. A shorter limit still wins and a restriction anywhere
// still wins, so a selection split across two requests answers the same as one
// that fit in a single request.
function mergeCoalesced(parts: EffectiveConstraintDetail[][]): EffectiveConstraintDetail[] {
  if (parts.length === 1) return parts[0];
  const byKey = new Map<string, EffectiveConstraintDetail>();
  for (const entry of parts.flat()) {
    const seen = byKey.get(entry.constraint);
    if (!seen) {
      byKey.set(entry.constraint, {...entry, sources: [...(entry.sources ?? [])]});
      continue;
    }
    seen.value =
      typeof seen.value === 'number' && typeof entry.value === 'number'
        ? Math.min(seen.value, entry.value)
        : Boolean(seen.value) || Boolean(entry.value);
    seen.sources = [...(seen.sources ?? []), ...(entry.sources ?? [])];
  }
  return Array.from(byKey.values());
}

// What a dialog reads. The three accessors mirror the free functions above,
// except that the two gates fail CLOSED while the answer is unknown: a
// permissive default shown during the fetch invites someone to act on it, and
// the backend would then reject the submit. A time limit cannot fail closed
// in any useful way, so a dialog offering one must keep its submit disabled
// while `pending` — `blocked` is that check.
export interface EffectiveConstraintsReader {
  /** True until every request backing this answer has resolved. */
  pending: boolean;
  /** Non-null if any request failed. The answer is then unknown, not empty. */
  error: Error | null;
  /** `pending || error != null` — the answer is not yet trustworthy. */
  blocked: boolean;
  /** Seconds, or null when no limit applies or the answer is unknown. */
  timeLimit(isOwner: boolean): number | null;
  /** True while the answer is unknown. */
  isReasonRequired(isOwner: boolean): boolean;
  /** True while the answer is unknown. */
  isSelfAddDisallowed(isOwner: boolean): boolean;
  /** One group's own answer, for marking individual rows. */
  forGroup(groupId: string | undefined | null): {
    timeLimit(isOwner: boolean): number | null;
    isReasonRequired(isOwner: boolean): boolean;
    isSelfAddDisallowed(isOwner: boolean): boolean;
  };
}

function reader(
  coalesced: EffectiveConstraintDetail[] | undefined,
  byGroup: Record<string, EffectiveConstraintDetail[]> | undefined,
  pending: boolean,
  error: Error | null,
): EffectiveConstraintsReader {
  // An absent list is unknown, not empty. `[]` is an answer -- nothing applies
  // to this group -- but `undefined` means nobody has told us yet, whether
  // because a request is in flight, because it failed, or because the payload
  // in hand does not carry the field. All three fail the gates closed.
  const at = (constraints: Constraints) => {
    const unknown = pending || error != null || constraints == null;
    return {
      timeLimit: (isOwner: boolean) => (unknown ? null : effectiveTimeLimit(constraints, isOwner)),
      isReasonRequired: (isOwner: boolean) => unknown || isReasonRequired(constraints, isOwner),
      isSelfAddDisallowed: (isOwner: boolean) => unknown || isSelfAddDisallowed(constraints, isOwner),
    };
  };
  return {
    pending,
    error,
    blocked: pending || error != null || coalesced == null,
    ...at(coalesced),
    // An id the response did not answer for is unknown too. The endpoint drops
    // ids naming a group that no longer exists, so a row whose group was
    // deleted since the page rendered must not read as unrestricted.
    forGroup: (groupId) => at(groupId ? (byGroup ?? {})[groupId] : undefined),
  };
}

/**
 * A reader over constraints carried on a payload already in hand, which is
 * never pending.
 *
 * `[]` means the payload answered "nothing applies". `undefined` or `null`
 * means it does not carry the answer -- an audit group reference has no such
 * field, and the create/update group responses send null, since only
 * `GET /api/groups/{id}` resolves it. Both read as unknown, so the gates fail
 * closed exactly as they do while a request is in flight.
 */
export function carriedConstraints(constraints: Constraints): EffectiveConstraintsReader {
  return reader(constraints ?? undefined, {}, false, null);
}

function useSplitQuery(ids: string[], mode: 'group_ids' | 'tag_ids'): EffectiveConstraintsReader {
  const batches = React.useMemo(() => chunked(ids), [ids.join(',')]);
  const results = useQueries({
    queries: batches.map((batch) => effectiveConstraintsQuery({queryParams: {[mode]: batch}})),
  });

  return React.useMemo(() => {
    if (batches.length === 0) return reader([], {}, false, null);
    const pending = results.some((r) => r.data === undefined && r.error == null);
    const error = (results.find((r) => r.error != null)?.error as Error | undefined) ?? null;
    if (pending || error != null) return reader(undefined, {}, pending, error);
    const parts = results.map((r) => r.data!);
    return reader(
      mergeCoalesced(parts.map((p) => p.coalesced ?? [])),
      Object.assign({}, ...parts.map((p) => p.by_group ?? {})),
      false,
      null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batches, results.map((r) => `${r.status}:${r.dataUpdatedAt}:${r.errorUpdatedAt}`).join('|')]);
}

/**
 * The constraints in force across `groupIds` taken together, plus each group's
 * own. Makes no request when nothing is selected, and splits a selection
 * larger than the endpoint's cap across several.
 */
export function useConstraintsForGroups(groupIds: (string | undefined | null)[]): EffectiveConstraintsReader {
  const ids = React.useMemo(() => stableIds(groupIds), [groupIds.join(',')]);
  return useSplitQuery(ids, 'group_ids');
}

/**
 * The constraints a set of tags would impose, for the case where no group
 * exists yet to ask about — approving a group request.
 */
export function useConstraintsForTags(tagIds: (string | undefined | null)[]): EffectiveConstraintsReader {
  const ids = React.useMemo(() => stableIds(tagIds), [tagIds.join(',')]);
  return useSplitQuery(ids, 'tag_ids');
}
