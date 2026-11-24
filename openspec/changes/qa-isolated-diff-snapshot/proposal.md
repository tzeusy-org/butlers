## Why

An isolated (Job) QA attempt runs in a pod that writes its own clone, so the dispatcher must not
run git there. Until now such an attempt recorded `diff_snapshot: []`, so its dossier showed no
diff even though the sealed export holds every change.

## What changes

- The dispatcher derives the isolated attempt's diff snapshot from its sealed export plus base
  trees and blobs read in-process from the dispatcher's own trusted repository (loose objects and
  packfiles, through the sealer's confined object store). No git process touches pod-written
  state.
- The snapshot is the net `base..head` diff in the existing line model, bounded like the sealer
  (file size, file count, line count, work budget). Binary or oversize files render a placeholder.
- Any refusal or error records `[]`, as before: a snapshot is display, never a gate.

## Not decided here

Showing a per-commit diff, and rename detection (a rename renders as a delete plus an add).

Tracking: `bu-ejow5p.20`.
