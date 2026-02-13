import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

const SHORTCUTS = [
  { keys: ["/"], description: "Open search" },
  { keys: ["Ctrl", "K"], description: "Open search" },
  { keys: ["g", "o"], description: "Go to Overview" },
  { keys: ["g", "b"], description: "Go to Butlers" },
  { keys: ["g", "s"], description: "Go to Sessions" },
  { keys: ["g", "t"], description: "Go to Timeline" },
  { keys: ["g", "r"], description: "Go to Traces" },
  { keys: ["g", "n"], description: "Go to Notifications" },
  { keys: ["g", "i"], description: "Go to Issues" },
  { keys: ["g", "a"], description: "Go to Audit Log" },
  { keys: ["g", "m"], description: "Go to Memory" },
  { keys: ["g", "c"], description: "Go to Contacts" },
  { keys: ["g", "h"], description: "Go to Health" },
] as const;

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
      {children}
    </kbd>
  );
}

export function ShortcutHints() {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="fixed bottom-4 right-4 z-50 h-8 w-8 rounded-full opacity-60 hover:opacity-100"
          aria-label="Keyboard shortcuts"
        >
          <span className="text-xs font-bold">?</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Keyboard Shortcuts</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 pt-2">
          {SHORTCUTS.map((shortcut, idx) => (
            <div key={idx} className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">{shortcut.description}</span>
              <div className="flex items-center gap-1">
                {shortcut.keys.map((key, kidx) => (
                  <span key={kidx} className="flex items-center gap-1">
                    {kidx > 0 && (
                      <span className="text-xs text-muted-foreground">+</span>
                    )}
                    <Kbd>{key}</Kbd>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
