/**
 * Utility functions for consuming Server-Sent Events (SSE) streams
 * from the conversation streaming endpoints.
 */

import type { ConversationSseEvent, ConversationSseEventType } from "@/api/types.ts";

/**
 * Parse a raw SSE line buffer into a structured event.
 * Returns null if the buffer doesn't form a complete event.
 */
export function parseSseChunk(chunk: string): ConversationSseEvent | null {
  const lines = chunk.split("\n");
  let eventType: ConversationSseEventType | null = null;
  let dataLine: string | null = null;

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim() as ConversationSseEventType;
    } else if (line.startsWith("data:")) {
      dataLine = line.slice(5).trim();
    }
  }

  if (dataLine == null) return null;

  let data: unknown = dataLine;
  try {
    data = JSON.parse(dataLine);
  } catch {
    // keep raw string
  }

  return {
    event: eventType ?? "token",
    data,
  };
}

/**
 * Read an SSE `Response` body and emit parsed events to `onEvent`.
 * Resolves when the stream ends or is aborted.
 */
export async function consumeSseStream(
  response: Response,
  onEvent: (event: ConversationSseEvent) => void,
): Promise<void> {
  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        if (!part.trim()) continue;
        const event = parseSseChunk(part);
        if (event) {
          onEvent(event);
        }
      }
    }

    // flush remaining
    if (buffer.trim()) {
      const event = parseSseChunk(buffer);
      if (event) onEvent(event);
    }
  } finally {
    reader.releaseLock();
  }
}
