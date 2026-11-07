import { useLocation } from "react-router";

/** Renders the router's current query string so tests can assert URL state. */
export function SearchEcho() {
  const location = useLocation();
  return <output data-testid="search">{location.search}</output>;
}
