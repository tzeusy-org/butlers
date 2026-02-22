export const OPEN_COMMAND_PALETTE_EVENT = "open-search";

export function dispatchOpenCommandPalette() {
  window.dispatchEvent(new CustomEvent(OPEN_COMMAND_PALETTE_EVENT));
}
