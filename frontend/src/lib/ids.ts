/**
 * UUIDv7 generation (RFC 9562) — time-ordered, globally unique.
 *
 * Format: 48-bit Unix ms timestamp | version=7 | 12-bit rand_a | variant=10 | 62-bit rand_b
 */
export function uuidv7(): string {
  const timestampMs = BigInt(Date.now());
  const randA = BigInt(Math.floor(Math.random() * 0x1000)); // 12 bits
  const randB = BigInt(
    Math.floor(Math.random() * 0x40000000) * 0x100000000 +
      Math.floor(Math.random() * 0x100000000),
  ); // 62 bits

  const hi = (timestampMs << 16n) | (7n << 12n) | randA;
  const lo = (2n << 62n) | (randB & 0x3fffffffffffffffn);

  const hex = hi.toString(16).padStart(16, "0") + lo.toString(16).padStart(16, "0");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
