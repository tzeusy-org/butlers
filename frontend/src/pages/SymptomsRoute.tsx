import { useSearchParams } from "react-router";

import SymptomsPage from "./SymptomsPage";

/** Keep URL state in the route so the symptoms view also works on its own. */
export default function SymptomsRoute() {
  const [searchParams] = useSearchParams();
  return <SymptomsPage nameFromQuery={searchParams.get("name") ?? ""} />;
}
