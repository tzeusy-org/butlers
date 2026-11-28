/**
 * Optional wall-clock shift for the whole test run (bu-5ii4gj.22).
 *
 * `VITEST_CLOCK_SHIFT_DAYS=60 npm run test` makes every un-pinned `Date.now()` and `new Date()`
 * read that many days in the future. The clock still advances (only the offset is added), timers
 * and `performance.now()` are untouched, and a test that pins its own clock with
 * `vi.setSystemTime` or `vi.useFakeTimers` is unaffected. A test that fails only under the shift
 * compares fixed fixture dates with the real clock: it passes today and fails when the calendar
 * reaches its fixtures. Pin its clock instead of moving the dates.
 */

const raw = process.env.VITEST_CLOCK_SHIFT_DAYS;
const days = raw === undefined || raw === "" ? 0 : Number(raw);

if (!Number.isFinite(days)) {
  throw new Error(`VITEST_CLOCK_SHIFT_DAYS must be a number of days, got ${JSON.stringify(raw)}`);
}

if (days !== 0 && !(globalThis.Date as { __clockShifted?: boolean }).__clockShifted) {
  const offsetMs = days * 86_400_000;
  const RealDate = globalThis.Date;

  class ShiftedDate extends RealDate {
    static __clockShifted = true;

    constructor(...args: unknown[]) {
      if (args.length === 0) super(RealDate.now() + offsetMs);
      else super(...(args as [number]));
    }

    static now(): number {
      return RealDate.now() + offsetMs;
    }
  }

  globalThis.Date = ShiftedDate as unknown as DateConstructor;
}
