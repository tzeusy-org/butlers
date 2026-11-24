// Produce and verify the bridge's complete offline module input context.
// Run this file with the original pinned Go builder; it uses only the stdlib.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
)

const builder = "sha256:bbb255b0e131db500cf0520adc97441d2260cf629c7fa7e39e025ddf53995a24"

type entry struct {
	Path   string `json:"path"`
	Type   string `json:"type"`
	SHA256 string `json:"sha256,omitempty"`
	Mode   uint32 `json:"mode"`
}
type manifest struct {
	Schema  string            `json:"schema"`
	Target  map[string]string `json:"target"`
	Project map[string]string `json:"project"`
	Inputs  []entry           `json:"inputs"`
}

func digest(file string) (string, error) {
	f, err := os.Open(file)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func target() map[string]string {
	return map[string]string{"go": "1.25.11", "platform": "linux-amd64", "builder": builder}
}

func projectPins(project string) (map[string]string, error) {
	result := map[string]string{}
	for _, name := range []string{"go.mod", "go.sum"} {
		file := filepath.Join(project, name)
		st, err := os.Lstat(file)
		if err != nil {
			return nil, err
		}
		if !st.Mode().IsRegular() {
			return nil, fmt.Errorf("nonregular project lock: %s", name)
		}
		result[name], err = digest(file)
		if err != nil {
			return nil, err
		}
	}
	return result, nil
}

func inventory(root string, canonical bool) ([]entry, error) {
	st, err := os.Lstat(root)
	if err != nil {
		return nil, err
	}
	if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("module cache must be a real directory")
	}
	if canonical && (st.Mode().Perm() != 0755 || st.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0) {
		return nil, errors.New("noncanonical module cache root")
	}
	result := []entry{}
	err = filepath.WalkDir(root, func(file string, item fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(root, file)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		if strings.ContainsAny(rel, "\\\x00\r\n") {
			return errors.New("unsafe module cache path")
		}
		info, err := item.Info()
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 ||
			(info.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 && (!info.IsDir() || canonical)) {
			return errors.New("unsupported module cache mode/link")
		}
		if info.IsDir() {
			if canonical && info.Mode().Perm() != 0755 {
				return errors.New("noncanonical module directory")
			}
			result = append(result, entry{Path: filepath.ToSlash(rel), Type: "directory", Mode: 0755})
			return nil
		}
		if !info.Mode().IsRegular() {
			return errors.New("nonregular module cache payload")
		}
		mode := uint32(0644)
		if info.Mode().Perm()&0111 != 0 {
			mode = 0755
		}
		if canonical && uint32(info.Mode().Perm()) != mode {
			return errors.New("noncanonical module payload mode")
		}
		hash, err := digest(file)
		if err != nil {
			return err
		}
		result = append(result, entry{Path: filepath.ToSlash(rel), Type: "file", SHA256: hash, Mode: mode})
		return nil
	})
	sort.Slice(result, func(i, j int) bool { return result[i].Path < result[j].Path })
	return result, err
}

func contained() error {
	if runtime.Version() != "go1.25.11" || runtime.GOOS != "linux" || runtime.GOARCH != "amd64" {
		return errors.New("wrong Go builder target")
	}
	interfaces, err := net.Interfaces()
	if err != nil {
		return err
	}
	if len(interfaces) != 1 || interfaces[0].Name != "lo" {
		return errors.New("offline phase needs only loopback")
	}
	routes, err := os.ReadFile("/proc/net/route")
	if err != nil {
		return err
	}
	if len(strings.Split(strings.TrimSpace(string(routes)), "\n")) != 1 {
		return errors.New("offline phase has an IPv4 route")
	}
	routes, err = os.ReadFile("/proc/net/ipv6_route")
	if err != nil {
		return err
	}
	for _, line := range strings.Split(strings.TrimSpace(string(routes)), "\n") {
		fields := strings.Fields(line)
		if len(fields) > 0 && fields[len(fields)-1] != "lo" {
			return errors.New("offline phase has an IPv6 route")
		}
	}
	return nil
}

func verify(project, artifacts string) error {
	root, err := os.Lstat(artifacts)
	if err != nil {
		return err
	}
	if !root.IsDir() || root.Mode()&os.ModeSymlink != 0 || root.Mode().Perm() != 0755 || root.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return errors.New("noncanonical Go context root")
	}
	file := filepath.Join(artifacts, "provenance.json")
	st, err := os.Lstat(file)
	if err != nil {
		return err
	}
	if !st.Mode().IsRegular() || st.Mode().Perm() != 0644 || st.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return errors.New("noncanonical Go provenance")
	}
	f, err := os.Open(file)
	if err != nil {
		return err
	}
	defer f.Close()
	decoder := json.NewDecoder(f)
	decoder.DisallowUnknownFields()
	var value manifest
	if err := decoder.Decode(&value); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return errors.New("trailing Go provenance data")
	}
	pins, err := projectPins(project)
	if err != nil {
		return err
	}
	if value.Schema != "butlers.go-artifacts/v1" || !reflect.DeepEqual(value.Target, target()) || !reflect.DeepEqual(value.Project, pins) {
		return errors.New("Go target/project lock drift")
	}
	actual, err := inventory(filepath.Join(artifacts, "modules"), true)
	if err != nil {
		return err
	}
	if len(actual) == 0 || !reflect.DeepEqual(actual, value.Inputs) {
		return errors.New("Go cache payload population/hash/mode drift")
	}
	items, err := os.ReadDir(artifacts)
	if err != nil {
		return err
	}
	if len(items) != 2 || items[0].Name() != "modules" || items[1].Name() != "provenance.json" {
		return errors.New("foreign Go context entry")
	}
	return nil
}

func copyTree(source, destination string) error {
	return filepath.WalkDir(source, func(file string, item fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(source, file)
		if err != nil {
			return err
		}
		to := filepath.Join(destination, rel)
		info, err := item.Info()
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 ||
			(!info.IsDir() && info.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0) {
			return errors.New("unsupported source mode/link")
		}
		if info.IsDir() {
			if err := os.Mkdir(to, 0755); err != nil {
				return err
			}
			return os.Chmod(to, 0755)
		}
		if !info.Mode().IsRegular() {
			return errors.New("unsupported source payload")
		}
		in, err := os.Open(file)
		if err != nil {
			return err
		}
		defer in.Close()
		out, err := os.OpenFile(to, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(out, in)
		closeErr := out.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
		mode := fs.FileMode(0644)
		if info.Mode().Perm()&0111 != 0 {
			mode = 0755
		}
		return os.Chmod(to, mode)
	})
}

func closeContext(project, cache, output string) error {
	if err := contained(); err != nil {
		return err
	}
	resolvedParent, err := filepath.EvalSymlinks(filepath.Dir(output))
	if err != nil {
		return err
	}
	output, err = filepath.Abs(filepath.Join(resolvedParent, filepath.Base(output)))
	if err != nil {
		return err
	}
	for _, root := range []string{project, cache} {
		resolved, err := filepath.EvalSymlinks(root)
		if err != nil {
			return err
		}
		resolved, err = filepath.Abs(resolved)
		if err != nil {
			return err
		}
		for _, pair := range [][2]string{{resolved, output}, {output, resolved}} {
			rel, err := filepath.Rel(pair[0], pair[1])
			if err != nil {
				return err
			}
			if rel == "." || (rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))) {
				return errors.New("Go output overlaps a source root")
			}
		}
	}
	if _, err := os.Lstat(output); !os.IsNotExist(err) {
		return errors.New("Go output already exists or is unavailable")
	}
	inputs, err := inventory(cache, false)
	if err != nil {
		return err
	}
	if len(inputs) == 0 {
		return errors.New("empty Go module cache")
	}
	pins, err := projectPins(project)
	if err != nil {
		return err
	}
	work, err := os.MkdirTemp(filepath.Dir(output), "go-artifacts-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(work)
	context := filepath.Join(work, "context")
	if err := os.Mkdir(context, 0755); err != nil {
		return err
	}
	if err := os.Chmod(context, 0755); err != nil {
		return err
	}
	if err := copyTree(cache, filepath.Join(context, "modules")); err != nil {
		return err
	}
	value := manifest{"butlers.go-artifacts/v1", target(), pins, inputs}
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(context, "provenance.json"), append(raw, '\n'), 0644); err != nil {
		return err
	}
	if err := os.Chmod(filepath.Join(context, "provenance.json"), 0644); err != nil {
		return err
	}
	if err := verify(project, context); err != nil {
		return err
	}
	// Compile from an independent original source copy, never the public seed's
	// private go.sum (go mod download all may add checksum-only records there).
	check := filepath.Join(work, "project")
	if err := copyTree(project, check); err != nil {
		return err
	}
	before, err := inventory(check, true)
	if err != nil {
		return err
	}
	env := []string{"PATH=" + filepath.Join(runtime.GOROOT(), "bin") + ":/usr/bin:/bin", "LANG=C.UTF-8", "GOENV=off", "GOTOOLCHAIN=local", "GOVCS=off", "GOPROXY=off", "GOSUMDB=off", "CGO_ENABLED=0", "GOOS=linux", "GOARCH=amd64", "GOMODCACHE=" + filepath.Join(context, "modules"), "GOCACHE=" + filepath.Join(work, "build-cache")}
	if home, ok := os.LookupEnv("HOME"); ok {
		env = append(env, "HOME="+home)
	}
	for _, args := range [][]string{{"mod", "verify"}, {"mod", "tidy", "-diff"}, {"build", "-mod=readonly", "-trimpath", "-buildvcs=false", "-ldflags=-s -w", "-o", filepath.Join(work, "whatsapp-bridge"), "./cmd/bridge"}} {
		command := exec.Command(filepath.Join(runtime.GOROOT(), "bin/go"), args...)
		command.Dir, command.Env, command.Stdout, command.Stderr = check, env, os.Stdout, os.Stderr
		if err := command.Run(); err != nil {
			return err
		}
	}
	after, err := inventory(check, true)
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(before, after) {
		return errors.New("offline build mutated original source")
	}
	if err := verify(project, context); err != nil {
		return err
	}
	if _, err := os.Lstat(output); !os.IsNotExist(err) {
		return errors.New("Go output appeared during production")
	}
	return os.Rename(context, output)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "expected close or verify")
		os.Exit(2)
	}
	parser := flag.NewFlagSet(os.Args[1], flag.ExitOnError)
	project := parser.String("project", "", "original bridge source")
	artifacts := parser.String("artifacts", "", "closed Go context")
	cache := parser.String("cache", "", "public seeded GOMODCACHE")
	output := parser.String("output", "", "new context destination")
	parser.Parse(os.Args[2:])
	var err error
	if *project == "" || parser.NArg() != 0 {
		err = errors.New("project is required; no positional arguments")
	} else if os.Args[1] == "close" && *cache != "" && *output != "" && *artifacts == "" {
		err = closeContext(*project, *cache, *output)
	} else if os.Args[1] == "verify" && *artifacts != "" && *cache == "" && *output == "" {
		err = contained()
		if err == nil {
			err = verify(*project, *artifacts)
		}
	} else {
		err = errors.New("invalid Go context operation")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("GO_ARTIFACTS_" + strings.ToUpper(os.Args[1]) + "_COMPLETE")
}
