package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func write(t *testing.T, file, value string, mode os.FileMode) {
	t.Helper()
	if err := os.WriteFile(file, []byte(value), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(file, mode); err != nil {
		t.Fatal(err)
	}
}

func fixture(t *testing.T) (string, string) {
	t.Helper()
	root := t.TempDir()
	project, artifacts := filepath.Join(root, "project"), filepath.Join(root, "artifacts")
	for _, dir := range []string{project, artifacts, filepath.Join(artifacts, "modules")} {
		if err := os.Mkdir(dir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(dir, 0755); err != nil {
			t.Fatal(err)
		}
	}
	write(t, filepath.Join(project, "go.mod"), "module owned\n\ngo 1.25.0\n", 0644)
	write(t, filepath.Join(project, "go.sum"), "owned synthetic lock\n", 0644)
	write(t, filepath.Join(artifacts, "modules", "payload"), "owned complete fixture bytes\n", 0755)
	inputs, err := inventory(filepath.Join(artifacts, "modules"), true)
	if err != nil {
		t.Fatal(err)
	}
	pins, err := projectPins(project)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(manifest{"butlers.go-artifacts/v1", target(), pins, inputs})
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(artifacts, "provenance.json"), string(raw), 0644)
	return project, artifacts
}

func TestVerifyMetadataHealthyAndDrift(t *testing.T) {
	for _, kind := range []string{"healthy", "lock", "hash", "mode", "foreign-file", "foreign-directory", "missing", "link", "unknown-field", "target", "context-mode", "provenance-mode"} {
		t.Run(kind, func(t *testing.T) {
			project, artifacts := fixture(t)
			if err := verify(project, artifacts); err != nil {
				t.Fatalf("healthy companion: %v", err)
			}
			payload := filepath.Join(artifacts, "modules", "payload")
			switch kind {
			case "context-mode":
				if err := os.Chmod(artifacts, os.ModeSetgid|0755); err != nil {
					t.Fatal(err)
				}
			case "provenance-mode":
				if err := os.Chmod(filepath.Join(artifacts, "provenance.json"), os.ModeSetuid|0644); err != nil {
					t.Fatal(err)
				}
			case "lock":
				write(t, filepath.Join(project, "go.sum"), "changed", 0644)
			case "hash":
				write(t, payload, "changed", 0755)
			case "mode":
				if err := os.Chmod(payload, 0644); err != nil {
					t.Fatal(err)
				}
			case "foreign-file":
				write(t, filepath.Join(artifacts, "modules", "foreign"), "owned", 0644)
			case "foreign-directory":
				file := filepath.Join(artifacts, "modules", "foreign")
				if err := os.Mkdir(file, 0755); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(file, 0755); err != nil {
					t.Fatal(err)
				}
			case "missing":
				if err := os.Remove(payload); err != nil {
					t.Fatal(err)
				}
			case "link":
				if err := os.Remove(payload); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(filepath.Join(project, "go.mod"), payload); err != nil {
					t.Fatal(err)
				}
			case "unknown-field", "target":
				file := filepath.Join(artifacts, "provenance.json")
				raw, err := os.ReadFile(file)
				if err != nil {
					t.Fatal(err)
				}
				var value map[string]any
				if err := json.Unmarshal(raw, &value); err != nil {
					t.Fatal(err)
				}
				if kind == "unknown-field" {
					value["unknown"] = true
				} else {
					value["target"].(map[string]any)["go"] = "0.0.0"
				}
				raw, err = json.Marshal(value)
				if err != nil {
					t.Fatal(err)
				}
				write(t, file, string(raw), 0644)
			}
			err := verify(project, artifacts)
			if kind == "healthy" && err != nil {
				t.Fatal(err)
			}
			if kind != "healthy" && err == nil {
				t.Fatal("drift accepted")
			}
		})
	}
}

func TestCopyPreservesExecutableBytesAndClearsDirectoryPrivileges(t *testing.T) {
	root := t.TempDir()
	source, dest := filepath.Join(root, "source"), filepath.Join(root, "copy")
	if err := os.Mkdir(source, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(source, os.ModeSetgid|0755); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(source, "executable"), "owned executable", 0751)
	write(t, filepath.Join(source, "data"), "owned data", 0640)
	if err := copyTree(source, dest); err != nil {
		t.Fatal(err)
	}
	actual, err := inventory(dest, true)
	if err != nil {
		t.Fatal(err)
	}
	expected, err := inventory(source, false)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(actual, expected) {
		t.Fatal("normalized bytes/modes differ")
	}
	a, err := os.Stat(filepath.Join(source, "executable"))
	if err != nil {
		t.Fatal(err)
	}
	b, err := os.Stat(filepath.Join(dest, "executable"))
	if err != nil {
		t.Fatal(err)
	}
	if os.SameFile(a, b) {
		t.Fatal("copy shares source inode")
	}
	write(t, filepath.Join(source, "executable"), "mutated seed", 0751)
	after, err := inventory(dest, true)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(actual, after) {
		t.Fatal("later seed mutation changed closed copy")
	}
	if _, err := inventory(source, true); err == nil {
		t.Fatal("noncanonical closed mode accepted")
	}
}

func TestRefuseLinksAndPrivilegedRegularInputs(t *testing.T) {
	for _, kind := range []string{"link", "setid-file"} {
		t.Run(kind, func(t *testing.T) {
			source := t.TempDir()
			file := filepath.Join(source, "payload")
			if kind == "link" {
				if err := os.Symlink("missing", file); err != nil {
					t.Fatal(err)
				}
			} else {
				write(t, file, "owned", os.ModeSetuid|0755)
			}
			if _, err := inventory(source, false); err == nil {
				t.Fatal("unsafe public input accepted")
			}
		})
	}
}
