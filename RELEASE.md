# Release Process

This document describes the release process for Butlers, including how to create releases and roll back to a previous version.

## Version Management

The single source of truth for the version is `pyproject.toml`:

```toml
[project]
version = "0.1.0"
```

Version format follows **semantic versioning** (`MAJOR.MINOR.PATCH`):

- `MAJOR` — breaking changes to the butler API or data model
- `MINOR` — new features (backward-compatible)
- `PATCH` — bug fixes and small improvements

## Creating a Release

### 1. Bump the version

Bump the patch segment automatically:

```bash
make bump-version
```

Or specify an explicit version:

```bash
make bump-version VERSION=1.2.0
```

### 2. Commit the version bump

```bash
VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
git add pyproject.toml
git commit -m "chore: bump version to $VERSION"
```

### 3. Create and push the release tag

```bash
make release-tag
git push origin v<VERSION>
```

For example, if `pyproject.toml` says `version = "1.2.0"`:

```bash
git push origin v1.2.0
```

### 4. Automated CI takes over

Pushing a `v*` tag triggers the **Release** GitHub Actions workflow
(`.github/workflows/release.yml`), which:

1. Verifies `pyproject.toml` version matches the tag
2. Generates a changelog from conventional commits via `git-cliff`
3. Builds and pushes a Docker image to GitHub Container Registry (GHCR):
   - `ghcr.io/tzeusy/butlers:1.2.0`
   - `ghcr.io/tzeusy/butlers:1.2`
   - `ghcr.io/tzeusy/butlers:latest`
4. Creates a GitHub Release with the generated changelog

## Rolling Back

With tagged images in GHCR, rolling back to a previous version is a two-step process.

### Step 1: Pull the previous image

```bash
docker pull ghcr.io/tzeusy/butlers:<PREVIOUS_VERSION>
```

For example, to roll back to `1.1.3`:

```bash
docker pull ghcr.io/tzeusy/butlers:1.1.3
```

### Step 2: Restart with the previous image

Update your `docker-compose.yml` or runtime config to reference the pinned version, then restart:

```bash
# If using docker compose — set the image tag in your environment or compose override:
BUTLERS_IMAGE=ghcr.io/tzeusy/butlers:1.1.3 docker compose up -d

# Or pull and restart a single container:
docker stop butlers
docker rm butlers
docker run -d --name butlers \
  -v /path/to/config:/etc/butler:ro \
  ghcr.io/tzeusy/butlers:1.1.3
```

### Finding available versions

List all available image tags via the GitHub Container Registry UI:

```
https://github.com/tzeusy/butlers/pkgs/container/butlers
```

Or via the GitHub CLI:

```bash
gh api /orgs/tzeusy/packages/container/butlers/versions \
  --jq '.[].metadata.container.tags[]' | sort -V
```

## Changelog

The `CHANGELOG.md` is generated automatically from conventional commits using
[git-cliff](https://git-cliff.org/) and is included in each GitHub Release.

To preview the changelog for unreleased commits locally:

```bash
# Install git-cliff (if not present)
cargo install git-cliff
# or: brew install git-cliff

# Preview changes since the last tag
git cliff --unreleased
```

## Conventional Commit Reference

Releases use [Conventional Commits](https://www.conventionalcommits.org/) to
auto-generate changelogs:

| Prefix | Changelog section |
|---|---|
| `feat:` | Features |
| `fix:` | Bug Fixes |
| `perf:` | Performance |
| `refactor:` | Refactoring |
| `test:` | Testing |
| `docs:` | Documentation |
| `chore:` | Miscellaneous |
| `ci:` | CI/CD |
