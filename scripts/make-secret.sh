#!/usr/bin/env bash
#
# Create and encrypt k8s/secret.sops.yaml.
#
# This is the one step that cannot live in the repository, because the age key
# deliberately does not. Run it on a machine that has the key
# (~/.config/sops/age/home-cluster.agekey), then commit the encrypted result.
#
#   ./scripts/make-secret.sh
#   ./scripts/make-secret.sh --api-key sk-ant-...
#
# The script exists mainly to remove two ways of getting this wrong by hand:
# the database password appears in two places that must match, and a plaintext
# secret must never survive a failure long enough to be committed.

set -o errexit
set -o nounset
set -o pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="$repo_root/k8s/secret.sops.yaml"
template="$repo_root/k8s/secret.sops.yaml.example"

api_key=""
while [ $# -gt 0 ]; do
  case "$1" in
    --api-key) api_key="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v sops >/dev/null 2>&1 || {
  echo "error: sops is not installed. See https://github.com/getsops/sops" >&2
  exit 1
}

# Refuse to clobber. Rotating a value means decrypting the existing file
# (`sops k8s/secret.sops.yaml`), not regenerating it — regenerating would change
# the database password and leave PostgreSQL holding the old one.
if [ -e "$target" ]; then
  echo "error: $target already exists." >&2
  echo "       To change a value, edit it in place:  sops k8s/secret.sops.yaml" >&2
  exit 1
fi

[ -f "$template" ] || { echo "error: missing $template" >&2; exit 1; }

if [ -z "$api_key" ]; then
  # Without a terminal there is nothing to prompt on, and `read` would just hit
  # EOF and abort under `set -e` with no explanation. Say so instead.
  if [ ! -t 0 ]; then
    echo "error: no API key given, and no terminal to prompt on." >&2
    echo "       Pass it explicitly:  ./scripts/make-secret.sh --api-key sk-ant-..." >&2
    exit 1
  fi
  # -s so the key is not echoed to the terminal or captured in shell history.
  read -r -s -p "Anthropic API key (input hidden): " api_key
  echo
fi
case "$api_key" in
  sk-ant-*) ;;
  "") echo "error: no API key given" >&2; exit 1 ;;
  *) echo "warning: that does not look like an Anthropic key (expected sk-ant-...)" >&2 ;;
esac

# 40 alphanumerics. Deliberately no punctuation: the password is substituted
# into a connection URL, where a `/`, `@` or `#` would silently change how the
# URL parses rather than failing outright.
#
# `head` reads a bounded chunk FIRST rather than truncating at the end of the
# pipeline. Written the other way round -- `tr < /dev/urandom | head -c 40` --
# head closes the pipe, tr dies of SIGPIPE, and `set -o pipefail` turns that
# into a failure of the whole script.
password="$(head -c 1024 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-40)"
if [ "${#password}" -ne 40 ]; then
  echo "error: could not generate a password (got ${#password} characters)" >&2
  exit 1
fi

# Any failure from here on must not leave a readable secret on disk.
cleanup() {
  if [ -e "$target" ] && ! grep -q 'ENC\[' "$target" 2>/dev/null; then
    rm -f "$target"
    echo "removed the unencrypted $target" >&2
  fi
}
trap cleanup EXIT

umask 077
sed -e '/^#/d' \
    -e "s|sk-ant-REPLACE-ME|${api_key}|" \
    -e "s|REPLACE-WITH-A-LONG-RANDOM-STRING|${password}|g" \
    "$template" > "$target"

# The password appears twice — as postgres-password and inside database-url —
# and PostgreSQL will simply refuse the connection if they drift apart.
occurrences="$(grep -c -- "$password" "$target" || true)"
if [ "$occurrences" -ne 2 ]; then
  echo "error: expected the password in 2 places, found $occurrences" >&2
  exit 1
fi
grep -q 'REPLACE' "$target" && { echo "error: a placeholder was left unfilled" >&2; exit 1; }

sops --encrypt --in-place "$target"

encrypted="$(grep -c 'ENC\[' "$target" || true)"
if [ "$encrypted" -lt 1 ]; then
  echo "error: sops reported success but the file is not encrypted" >&2
  exit 1
fi

trap - EXIT
echo "wrote $target ($encrypted encrypted values)"
echo
echo "Check it reads the way you expect, then commit it:"
echo "  git add k8s/secret.sops.yaml && git commit -m 'Add the deployment secret'"
