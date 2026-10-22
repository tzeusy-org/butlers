import { Link } from "react-router";
import type { Fact, MemoryRule } from "@/api/types";
import { useTimezone } from "@/components/ui/timezone-context";
import { formatDayStamp } from "@/lib/memory-derived";

/** Display server-stamped content authorship, independently of the record's subject. */
export function MemoryProvenanceLine({ record }: { record: Fact | MemoryRule }) {
  const tz = useTimezone();
  const authority = record.content_authority;
  const rule = "maturity" in record ? record : null;
  const held = rule?.held === true && !rule.endorsed_at && !rule.retired_at
    && rule.metadata.forgotten !== true;
  let author;
  switch (authority) {
    case "owner": author = "You told me"; break;
    case "owner_device": author = "You told me from an owner device"; break;
    case "third_party":
      author = record.authority_entity_id
        ? <><Link className="underline underline-offset-2" to={`/entities/${record.authority_entity_id}`}>
          {record.authority_entity_name ?? "An unresolved sender"}</Link> told me</>
        : "Someone else told me (unverified sender)";
      break;
    case "system": author = "Recorded by the system"; break;
    case "mixed": author = "Combined from several sources"; break;
    default: author = "Unverified source";
  }

  return <div aria-label="Source attribution" className="text-[13px] leading-relaxed text-[var(--mfg)]">
    {author}
    {authority !== "third_party" && record.authority_entity_id && <> · Source: <Link
      className="underline underline-offset-2" to={`/entities/${record.authority_entity_id}`}>
      {record.authority_entity_name ?? "Unresolved entity"}</Link></>}
    {rule?.endorsed_at && <>
      {" · "}{rule.endorsed_by
        ? <>Endorsed by <Link className="underline underline-offset-2" to={`/entities/${rule.endorsed_by}`}>
          {rule.endorsed_by_name ?? "Unresolved endorser"}</Link></>
        : "Endorsement recorded"}
      {" on "}<time dateTime={rule.endorsed_at}>{formatDayStamp(rule.endorsed_at, tz)}</time>
    </>}
    {held && <> · <Link className="underline underline-offset-2" to="/memory?register=rules&held=true">
      Guidance waiting for you</Link></>}
  </div>;
}
