import { useRef, useState } from "react";
import { logoutOwner } from "@/api/owner-session";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger } from "@/components/ui/dialog";

export function OwnerSessionMenu() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const running = useRef(false);
  async function signOut(all: boolean) {
    if (running.current) return;
    running.current = true; setBusy(true); setMessage("Signing out…");
    try { await logoutOwner(all); }
    catch { setMessage("Sign out could not be confirmed. Check the connection and try again."); }
    finally { running.current = false; setBusy(false); }
  }
  return <Dialog>
    <DialogTrigger asChild><Button variant="ghost" size="sm">Owner session</Button></DialogTrigger>
    <DialogContent><DialogHeader><DialogTitle>Owner session</DialogTitle><DialogDescription>Sign out of this browser or revoke all browser sessions. Your passkey remains available for the next sign-in.</DialogDescription></DialogHeader>
      <p role="status" aria-live="polite" className="text-sm">{message}</p>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={() => void signOut(true)}>Sign out all browsers</Button><Button disabled={busy} onClick={() => void signOut(false)}>Sign out this browser</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
