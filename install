#!/usr/bin/env bash
# Install HydraDB CLI from GitHub Releases.
# Usage:
#   curl -fsSL https://cli.hydradb.com/install | bash
#
# Optional:
#   HYDRADB_CLI_VERSION=0.1.1 curl -fsSL ... | bash
#   HYDRADB_CLI_FORCE=1 curl -fsSL ... | bash
#
# The wheel is downloaded from the GitHub release; its runtime dependencies
# (typer, httpx, rich, hydradb-sdk) still resolve from PyPI as usual.

set -euo pipefail

REPO="${HYDRADB_CLI_REPO:-hydra-db/hydradb-cli}"
PACKAGE_NAME="${HYDRADB_CLI_PACKAGE:-hydradb-cli}"
COMMAND_NAME="${HYDRADB_CLI_COMMAND:-hydradb}"
VERSION="${HYDRADB_CLI_VERSION:-}"
FORCE="${HYDRADB_CLI_FORCE:-0}"

info() {
  printf '\033[1;34m[hydradb]\033[0m %s\n' "$1"
}

warn() {
  printf '\033[1;33m[hydradb]\033[0m %s\n' "$1"
}

fail() {
  printf '\033[1;31m[hydradb]\033[0m %s\n' "$1" >&2
  exit 1
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
      then
        printf '%s' "$candidate"
        return 0
      fi
    fi
  done

  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  fail "Python 3.10 or higher is required. Install Python 3.10+ and run this again."
fi

PYTHON_VERSION="$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
info "Using Python ${PYTHON_VERSION}"

# Resolve the newest published release by following the /releases/latest
# redirect, which lands on /releases/tag/<tag>. This uses no API quota. The tag
# is returned verbatim: releases are conventionally tagged `vX.Y.Z`, but an
# unprefixed tag is a valid release too, and rebuilding the tag from the version
# instead of using the resolved one would send us to a URL that does not exist.
resolve_latest_tag() {
  "$PYTHON" - "$REPO" <<'PY'
import sys
import urllib.request

repo = sys.argv[1]
url = f"https://github.com/{repo}/releases/latest"
try:
    with urllib.request.urlopen(url, timeout=30) as response:
        final = response.url
except Exception as exc:  # noqa: BLE001 - any failure means "could not resolve"
    print(f"  reason: {exc}", file=sys.stderr)
    raise SystemExit(1) from None

tag = final.rstrip("/").rsplit("/", 1)[-1]
if not tag or tag == "latest":
    print(f"  reason: no published release found at {url}", file=sys.stderr)
    raise SystemExit(1)

print(tag)
PY
}

if [ -n "$VERSION" ]; then
  # A pinned version is not a tag, so both spellings have to be tried.
  CANDIDATE_TAGS="v${VERSION} ${VERSION}"
else
  info "Resolving latest release of ${REPO}"
  TAG="$(resolve_latest_tag || true)"
  if [ -z "$TAG" ]; then
    fail "Could not resolve the latest release. Pin one with HYDRADB_CLI_VERSION=<x.y.z> and try again."
  fi
  VERSION="${TAG#v}"
  CANDIDATE_TAGS="$TAG"
fi

# setuptools normalises the distribution name in artifact filenames.
DIST_NAME="$(printf '%s' "$PACKAGE_NAME" | tr '-' '_')"
WHEEL_NAME="${DIST_NAME}-${VERSION}-py3-none-any.whl"

info "Installing ${PACKAGE_NAME} ${VERSION}"

# Quiet: a miss on the first candidate is expected, so the diagnostic is the
# list of URLs tried, reported once below.
asset_exists() {
  "$PYTHON" - "$1" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

request = urllib.request.Request(sys.argv[1], method="HEAD")
with urllib.request.urlopen(request, timeout=30):
    pass
PY
}

WHEEL_URL=""
TRIED=""
for tag in $CANDIDATE_TAGS; do
  candidate="https://github.com/${REPO}/releases/download/${tag}/${WHEEL_NAME}"
  TRIED="${TRIED}
  ${candidate}"
  if asset_exists "$candidate"; then
    WHEEL_URL="$candidate"
    break
  fi
done

if [ -z "$WHEEL_URL" ]; then
  fail "No wheel found for version ${VERSION}. Tried:${TRIED}

  Check https://github.com/${REPO}/releases for available versions."
fi

install_with_pipx() {
  if ! command -v pipx >/dev/null 2>&1; then
    return 1
  fi

  info "Installing with pipx"

  # The spec is a URL, so pipx cannot resolve an upgrade from it; a forced
  # install is the only path that reliably replaces an existing version.
  if [ "$FORCE" = "1" ] || pipx list 2>/dev/null | grep -q "package ${PACKAGE_NAME} "; then
    pipx install --force "$WHEEL_URL"
  else
    pipx install "$WHEEL_URL"
  fi

  return 0
}

install_with_pip_user() {
  info "pipx not found. Installing with pip --user"

  "$PYTHON" -m pip install --user --upgrade pip >/dev/null

  if [ "$FORCE" = "1" ]; then
    "$PYTHON" -m pip install --user --force-reinstall "$WHEEL_URL"
  else
    "$PYTHON" -m pip install --user --upgrade "$WHEEL_URL"
  fi
}

if ! install_with_pipx; then
  install_with_pip_user
fi

USER_BIN="$($PYTHON - <<'PY'
import os
import site
import sys
if os.name == "nt":
    print(os.path.join(site.USER_BASE, "Scripts"))
else:
    print(os.path.join(site.USER_BASE, "bin"))
PY
)"

if command -v "$COMMAND_NAME" >/dev/null 2>&1; then
  info "Installed successfully"
  "$COMMAND_NAME" --version || true
  exit 0
fi

if [ -x "${USER_BIN}/${COMMAND_NAME}" ]; then
  info "Installed successfully"
  "${USER_BIN}/${COMMAND_NAME}" --version || true
  warn "${USER_BIN} is not on your PATH. Add this line to your shell profile:"
  printf '\n  export PATH="%s:$PATH"\n\n' "$USER_BIN"
  exit 0
fi

fail "Installation finished, but '${COMMAND_NAME}' was not found. Check your Python scripts directory or pipx path."
