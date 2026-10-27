const FENCE = /^\s*(`{3,}|~{3,})/gm;

/** Close a half-open fence in the render copy without changing persisted content. */
export function prepareStreamingMarkdown(content: string): string {
  const fences = Array.from(content.matchAll(FENCE), (match) => match[1]);
  if (fences.length % 2 === 0) return content;
  return `${content}\n\n${fences.at(-1)}`;
}

/** Markdown prose may link only to absolute HTTPS destinations. */
export function safeMarkdownUrl(value: string): string {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? value : "";
  } catch {
    return "";
  }
}
