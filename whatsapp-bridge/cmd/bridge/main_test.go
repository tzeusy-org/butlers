package main

import (
	"flag"
	"testing"
)

func TestBridgeFlagsDefaultToSelfContainedStandaloneSocket(t *testing.T) {
	t.Setenv("WA_LISTEN", "")

	fs, _, listenAddr := newBridgeFlagSet(flag.ContinueOnError)
	if err := fs.Parse(nil); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	if got, want := *listenAddr, "unix:///tmp/wa-bridge.sock"; got != want {
		t.Fatalf("default listen address = %q, want %q", got, want)
	}
}

func TestBridgeListenFlagOverridesEnvironment(t *testing.T) {
	t.Setenv("WA_LISTEN", "unix:///tmp/from-env.sock")

	fs, _, listenAddr := newBridgeFlagSet(flag.ContinueOnError)
	if err := fs.Parse([]string{"--listen", "unix:///tmp/from-flag.sock"}); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	if got, want := *listenAddr, "unix:///tmp/from-flag.sock"; got != want {
		t.Fatalf("listen address = %q, want %q", got, want)
	}
}
