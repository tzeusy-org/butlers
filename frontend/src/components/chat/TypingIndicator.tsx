/** Static typing indicator displayed while the butler is processing a response. */

export function TypingIndicator() {
  return (
    <div className="flex items-end gap-2 pb-2" aria-hidden="true">
      <div className="bg-muted rounded-2xl rounded-bl-sm px-4 py-3 flex items-center gap-1">
        <span className="size-1.5 rounded-full bg-muted-foreground" />
        <span className="size-1.5 rounded-full bg-muted-foreground" />
        <span className="size-1.5 rounded-full bg-muted-foreground" />
      </div>
    </div>
  );
}
