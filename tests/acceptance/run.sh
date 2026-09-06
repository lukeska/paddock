#!/usr/bin/env bash
# Post-reboot acceptance for a live Paddock installation.
#
# Every defect found in this project so far was found by running against a real
# system while the unit suite stayed green, so this exists to make that run
# repeatable instead of a sequence of hand-typed curl commands. It deploys the
# served fixtures, then checks both systemd managers, the writing contract, the
# sandbox denials, and every configured service.
#
# The web-server sections measure the promoted nginx tree and the running
# process rather than the generator that produced them. That distinction is the
# point: the two defects the nginx migration shipped both passed every unit test
# and every containerised check, because a container runs as root and compiles
# its paths elsewhere, so neither noticed a built-in default the desktop user
# cannot write.
#
# Read-only apart from the fixture directories it owns under ~/paddock-verify
# and three validated web-server reloads, which is the only way to measure that
# a reload drops no requests. Each one re-renders from unchanged state, so the
# sites served are the same before and after. It never uses sudo and never
# touches a real project.
set -uo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fixtures=${PADDOCK_VERIFY_DIR:-$HOME/paddock-verify}
# Defaults to the installed CLI, which is what should normally be measured.
# PADDOCK_BIN points it at a working tree so a change can be checked before
# it is packaged.
paddock=${PADDOCK_BIN:-paddock}
failures=0

pass() { printf '\033[32mPASS\033[0m  %-28s %s\n' "$1" "${2:-}"; }
fail() { printf '\033[31mFAIL\033[0m  %-28s %s\n' "$1" "${2:-}"; failures=$((failures + 1)); }
check() { if [ "$1" = 0 ]; then pass "$2" "${3:-}"; else fail "$2" "${3:-}"; fi; }

echo "== fixtures =="
for site in alpha beta; do
  directory="$fixtures/$site"
  if [ ! -f "$directory/.paddock.json" ]; then
    fail "fixture:$site" "no PHP selection; see the header of this script"
    continue
  fi
  # `.paddock.json` is written by `paddock php use`, which links nothing. On a
  # machine where the selection exists but the site does not, every check below
  # failed with a TLS handshake error — the catch-all correctly refusing a host
  # it does not serve, which describes the symptom and not the cause.
  if ! "$paddock" sites 2>/dev/null \
      | awk -v s="$site" '$1 == s { found = 1 } END { exit !found }'; then
    fail "fixture:$site" "not linked; run: (cd $directory && $paddock link)"
    continue
  fi
  install -Dm644 "$root/site/public/index.php" "$directory/public/index.php"
  install -Dm644 "$root/site/public/probe.php" "$directory/public/probe.php"
  # Reload safety is measured with a static file: a PHP request per iteration
  # would measure FPM's throughput instead of nginx's handover.
  install -Dm644 "$root/site/public/static.txt" "$directory/public/static.txt"
  linked_fixtures="${linked_fixtures:+$linked_fixtures }$site"
  pass "fixture:$site" "$directory"
done
# Downstream sections iterate what actually got deployed. Re-reporting a
# missing prerequisite once per check buries the one line that says what to do.
linked_fixtures=${linked_fixtures:-}

echo "== system units =="
# The PHP units are derived, not listed: which minors exist is user state, and
# a hardcoded pair reports a missing unit for a version this machine never
# installed while saying nothing about one it did. `report` builds the same
# list the CLI itself checks.
php_units=$("$paddock" report 2>/dev/null | python3 -c '
import json, sys
for runtime in json.load(sys.stdin)["php"]["runtimes"]:
    print(runtime["unit"])
' 2>/dev/null)
check "$([ -n "$php_units" ]; echo $?)" "units:php-runtimes" \
      "$(printf '%s' "$php_units" | tr '\n' ' ')"
for unit in paddock.target paddock-dns.service paddock-web.service $php_units; do
  state=$(systemctl is-active "$unit" 2>&1)
  check "$([ "$state" = active ]; echo $?)" "unit:$unit" "$state"
  # Only services carry NRestarts; a target reports nothing and would pass
  # vacuously. A unit that is up only because it restarted hides a failure.
  case "$unit" in
    *.service)
      restarts=$(systemctl show "$unit" -p NRestarts --value 2>/dev/null)
      check "$([ "$restarts" = 0 ]; echo $?)" "restarts:$unit" "NRestarts=${restarts:-unknown}"
      ;;
  esac
done

# The regression that cost a whole session: a namespace directive that works
# interactively and aborts at boot.
namespace=$(journalctl -b --no-pager 2>/dev/null | grep -c '226/NAMESPACE')
check "$([ "$namespace" = 0 ]; echo $?)" "boot:no-namespace-abort" "$namespace occurrences"

echo "== sites =="
[ -n "$linked_fixtures" ] || pass "sites" "no fixture is linked; skipped"
for site in $linked_fixtures; do
  expected=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['php'])" \
    "$fixtures/$site/.paddock.json" 2>/dev/null)
  [ -n "$expected" ] || { fail "site:$site" "no .paddock.json"; continue; }
  served=$(curl -fsS --max-time 15 "https://$site.test/" 2>&1)
  # The fixture prints a full version; compare on the selected minor.
  case "$served" in
    "$expected".*) pass "site:$site" "$served (selected $expected)" ;;
    *) fail "site:$site" "expected $expected.*, got '$served'" ;;
  esac
done

echo "== writing contract =="
[ -n "$linked_fixtures" ] || pass "writing contract" "no fixture is linked; skipped"
for site in $linked_fixtures; do
  output=$(curl -fsS --max-time 20 "https://$site.test/probe.php" 2>&1)
  if printf '%s' "$output" | grep -q '^RESULT=PASS$'; then
    pass "probe:$site" "$(printf '%s' "$output" | tr '\n' ' ')"
  else
    fail "probe:$site" "$(printf '%s' "$output" | tr '\n' ' ')"
  fi
done

state=${XDG_STATE_HOME:-$HOME/.local/state}/paddock
config_home=${XDG_CONFIG_HOME:-$HOME/.config}
web=$state/nginx
current=$web/current
# Everything nginx actually loaded, which is what these sections judge.
live=$(cat "$current/nginx.conf" "$current"/sites/*.conf "$current"/snippets/*.conf 2>/dev/null)

echo "== web boundary =="
# ADR 0011 carries ADR 0003's boundary forward unchanged: a fixed root-owned
# unit runs the server as the desktop user with only the capability it needs to
# bind a port below 1024, and PID 1 never interprets the generated site map.
for property in AmbientCapabilities CapabilityBoundingSet; do
  value=$(systemctl show paddock-web.service -p "$property" --value 2>/dev/null)
  check "$([ "$value" = cap_net_bind_service ]; echo $?)" "web:$property" \
        "${value:-empty}"
done

# nginx has no sd_notify, so it runs in the foreground. Type=notify would leave
# systemd waiting for a readiness signal that never arrives.
type_value=$(systemctl show paddock-web.service -p Type --value 2>/dev/null)
check "$([ "$type_value" = simple ]; echo $?)" "web:type" "Type=$type_value"

# Two ExecReload commands, `nginx -t` first: a configuration that fails the
# test must never reach the running process.
reload_commands=$(systemctl show paddock-web.service -p ExecReload 2>/dev/null \
  | grep -c 'argv\[\]=')
check "$([ "$reload_commands" = 2 ]; echo $?)" "web:reload-is-gated" \
      "$reload_commands ExecReload command(s)"
first_reload=$(systemctl show paddock-web.service -p ExecReload 2>/dev/null | head -1)
case "$first_reload" in
  *" -t "*) pass "web:reload-tests-first" "nginx -t precedes the signal" ;;
  *) fail "web:reload-tests-first" "first ExecReload is not a test" ;;
esac

echo "== web listeners =="
# Addresses only, with no `-p`: `ss` reports no process for these sockets even
# though they belong to this user, so attributing them would fail for a reason
# that has nothing to do with Paddock. The unit is already known to be active
# and unrestarted, and nothing else may hold these ports.
listeners=$(ss -H -lntu 2>/dev/null || true)
while read -r protocol address; do
  [ -n "$protocol" ] || continue
  found=$(printf '%s\n' "$listeners" | awk -v p="$protocol" -v a="$address" \
    '$1 == p && $5 == a' | head -1)
  check "$([ -n "$found" ]; echo $?)" "web:listen:$protocol:$address" \
        "$([ -n "$found" ] && echo bound || echo missing)"
done <<'LISTENERS'
tcp 127.0.0.1:80
tcp 127.0.0.1:443
udp 127.0.0.1:443
LISTENERS
# UDP 443 is HTTP/3. It is also why check-ports still has to look at it: with
# SO_REUSEPORT a second server shares the socket instead of failing to bind.
#
# ADR 0003 binds loopback only, so nothing may answer for these ports on any
# other address. A listen line that lost its 127.0.0.1 would publish every
# linked project on the network.
exposed=$(printf '%s\n' "$listeners" \
  | awk '$5 ~ /:(80|443)$/ && $5 !~ /^127\.0\.0\.1:/ { print $1" "$5 }' \
  | sort -u | tr '\n' ';')
check "$([ -z "$exposed" ]; echo $?)" "web:loopback-only" \
      "${exposed:-nothing on 80 or 443 outside loopback}"

echo "== web configuration =="
promoted=$(readlink -f "$current" 2>/dev/null)
case "$promoted" in
  "$web/generations/"*) pass "web:promoted" "${promoted#$web/}" ;;
  *) fail "web:promoted" "current does not resolve into generations/: ${promoted:-missing}" ;;
esac

# The class of defect this branch shipped twice. nginx creates a temporary
# directory for every proxying module compiled in and opens an access log, and
# the built-in paths for those sit outside anything the desktop user can write.
stray=$(printf '%s\n' "$live" \
  | grep -E '^[[:space:]]*(pid|error_log|lock_file|[a-z_]+_temp_path)[[:space:]]' \
  | grep -vF "$state" | tr -s ' \t' ' ' | tr '\n' ';' )
check "$([ -z "$stray" ]; echo $?)" "web:paths-inside-state" \
      "${stray:-every writable path is under $state}"

# nginx rejects a second reuseport for one address and port, and the anchor has
# to be the catch-all: it is the only block that never moves as sites change.
reuseports=$(printf '%s\n' "$live" | grep -c 'reuseport' || true)
check "$([ "$reuseports" = 1 ]; echo $?)" "web:reuseport-once" \
      "$reuseports occurrence(s)"
anchored=$(grep -c 'reuseport' "$current/sites/000-default.conf" 2>/dev/null || true)
check "$([ "${anchored:-0}" = 1 ]; echo $?)" "web:reuseport-anchor" \
      "on the catch-all"

# A listen line that lost its address would publish every linked project on
# the network.
off_listen=$(printf '%s\n' "$live" | grep -E '^[[:space:]]*listen[[:space:]]' \
  | grep -vF '127.0.0.1:' | tr -s ' \t' ' ' | tr '\n' ';')
check "$([ -z "$off_listen" ]; echo $?)" "web:listen-loopback" \
      "${off_listen:-every listen names 127.0.0.1}"

echo "== routing =="
# Without a default server the first block in the tree answers every unknown
# host, which would serve one project's files under another's name.
unknown=$(curl -sS --max-time 10 http://paddock-no-such-site.test/ 2>&1)
case "$unknown" in
  *"No Paddock site is linked"*) pass "route:catch-all" "explains itself" ;;
  *) fail "route:catch-all" "$(printf '%s' "$unknown" | head -c 120)" ;;
esac

for site in $linked_fixtures; do
  scheme=$("$paddock" sites 2>/dev/null | awk -v s="$site" '$1 == s { print $4 }')
  [ -n "$scheme" ] || continue

  # nginx would serve .env as plain text; the generated block denies dotfiles
  # whether or not the file is there.
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
    "$scheme://$site.test/.env" 2>/dev/null)
  check "$([ "$code" = 403 ]; echo $?)" "route:$site:dotfile" "HTTP $code"

  [ "$scheme" = https ] || continue
  # A secured site now answers on 80 and redirects. The previous server served
  # no plaintext listener for one at all.
  redirect=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' \
    --max-time 10 "http://$site.test/" 2>/dev/null)
  case "$redirect" in
    "301 https://$site.test/"*) pass "route:$site:https-redirect" "$redirect" ;;
    *) fail "route:$site:https-redirect" "${redirect:-no response}" ;;
  esac

  # Nothing reaches HTTP/3 without being told the port speaks it.
  advertised=$(curl -sk -D- -o /dev/null --max-time 10 "https://$site.test/" 2>/dev/null \
    | grep -i '^alt-svc:' | tr -d '\r')
  case "$advertised" in
    *'h3=":443"'*) pass "route:$site:alt-svc" "$advertised" ;;
    *) fail "route:$site:alt-svc" "${advertised:-no Alt-Svc header}" ;;
  esac
done

echo "== reload under load =="
# The claim ADR 0011 still owed evidence for. A validated reload must not drop
# a request, and the promoted generation must actually advance.
probe_site=${linked_fixtures%% *}
probe_scheme=$([ -n "$probe_site" ] && "$paddock" sites 2>/dev/null \
  | awk -v s="$probe_site" '$1 == s { print $4 }')
if [ -z "$probe_scheme" ]; then
  pass "reload:under-load" "no fixture is linked; skipped"
else
  misses=$(mktemp)
  total_file=$(mktemp)
  before=$(readlink -f "$current")
  (
    total=0
    finish=$(( $(date +%s) + 6 ))
    while [ "$(date +%s)" -lt "$finish" ]; do
      total=$((total + 1))
      curl -fsS -o /dev/null --max-time 5 \
        "$probe_scheme://$probe_site.test/static.txt" 2>/dev/null \
        || printf 'x' >>"$misses"
    done
    printf '%s' "$total" >"$total_file"
  ) &
  requester=$!
  reload_failures=0
  for _ in 1 2 3; do
    "$paddock" reload >/dev/null 2>&1 || reload_failures=$((reload_failures + 1))
    sleep 1
  done
  wait "$requester" 2>/dev/null
  dropped=$(wc -c <"$misses" 2>/dev/null | tr -d ' ')
  requests=$(cat "$total_file" 2>/dev/null)
  after=$(readlink -f "$current")
  rm -f "$misses" "$total_file"
  check "$([ "$reload_failures" = 0 ]; echo $?)" "reload:accepted" \
        "$reload_failures of 3 reloads failed"
  check "$([ "${dropped:-1}" = 0 ]; echo $?)" "reload:no-dropped-requests" \
        "${dropped:-unknown} failure(s) across ${requests:-0} requests and 3 reloads"
  check "$([ "$before" != "$after" ]; echo $?)" "reload:generation-advanced" \
        "${before##*/} -> ${after##*/}"
fi

echo "== project types =="
# Measured against the machine's real sites, including parked ones, because a
# misdetection is only visible against a project someone actually has.
site_types=$("$paddock" report 2>/dev/null | python3 -c '
import json, sys
for site in json.load(sys.stdin)["sites"]:
    print(site["name"], site.get("type", "-"), site.get("document_root", "-"))
' 2>/dev/null)
if [ -z "$site_types" ]; then
  fail "type:report" "no sites in the report"
else
  while read -r name type_name document_root; do
    [ -n "$name" ] || continue
    check "$([ -d "$document_root" ]; echo $?)" "type:$name" \
          "$type_name · $document_root"
    upstreams=$(grep -c 'fastcgi_pass' "$current/sites/$name.conf" 2>/dev/null || true)
    if [ "$type_name" = static ]; then
      # A static site must neither run PHP nor hand back its source, because
      # this is the type an unrecognised directory falls back to.
      check "$([ "${upstreams:-0}" = 0 ]; echo $?)" "type:$name:runs-nothing" \
            "${upstreams:-0} fastcgi_pass"
    else
      check "$([ "${upstreams:-0}" -ge 1 ]; echo $?)" "type:$name:has-php" \
            "${upstreams:-0} fastcgi_pass"
    fi
  done <<< "$site_types"
fi

echo "== per-site configuration =="
# ADR 0012's security claim, measured against what nginx loaded rather than
# what Paddock believes: a fragment that is not trusted contributes nothing.
declared=0
while read -r name _ _; do
  [ -n "$name" ] || continue
  summary=$("$paddock" config "$name" 2>/dev/null)
  fragment=$(printf '%s\n' "$summary" | awk '$1 == "project" { print $2 }')
  status=$(printf '%s\n' "$summary" | awk '$1 == "project" { print $3 }' | tr -d '()')
  conf="$current/sites/$name.conf"

  mine="$config_home/paddock/nginx/$name.custom.conf"
  if [ -f "$mine" ]; then
    check "$(grep -qF "$mine" "$conf" 2>/dev/null; echo $?)" "config:$name:mine" \
          "own fragment included"
  fi

  case "$fragment" in ""|none) continue ;; esac
  declared=$((declared + 1))
  included=$(grep -cF "$fragment" "$conf" 2>/dev/null || true)
  if [ "$status" = trusted ]; then
    check "$([ "${included:-0}" -ge 1 ]; echo $?)" "config:$name" \
          "$fragment trusted and included"
  else
    check "$([ "${included:-0}" = 0 ]; echo $?)" "config:$name" \
          "$fragment $status and not included"
  fi
done <<< "$site_types"
[ "$declared" = 0 ] && pass "config:project" "no site declares one; skipped"

echo "== supporting services =="
services=$("$paddock" services 2>/dev/null)
if [ -z "$services" ]; then
  pass "services" "none configured"
else
  # Lingering is what makes a user unit return after a reboot, so a service
  # that is active now proves nothing without it.
  linger=$(loginctl show-user "$(id -u)" --property=Linger --value 2>/dev/null)
  check "$([ "$linger" = yes ]; echo $?)" "services:linger" "$linger"
  # `paddock services` prints id, type, label, state, address, image. Reading
  # only the first three compared the *type* against "active" and parsed a port
  # out of the *label*, so every service failed twice over while `report` and
  # `doctor` both said the machine was healthy.
  while IFS=$'\t' read -r name type label state address image; do
    [ -n "$name" ] || continue
    check "$([ "$state" = active ]; echo $?)" "service:$name" \
          "$label ($type) $state $address"
    unit="paddock-service-$name.service"
    enabled=$(systemctl --user is-enabled "$unit" 2>&1)
    check "$([ "$enabled" = enabled ]; echo $?)" "service:$name:at-boot" "$enabled"
    # Published on loopback only; a routable bind would expose it to the LAN.
    port=${address##*:}
    listening=$(ss -ltn 2>/dev/null | grep ":$port ")
    case "$listening" in
      *127.0.0.1:"$port"*) pass "service:$name:loopback" "127.0.0.1:$port" ;;
      *) fail "service:$name:loopback" "${listening:-nothing listening on $port}" ;;
    esac
  done <<< "$services"
fi

echo "== machine contract =="
# The Omarchy plugin parses this and nothing else, so it has to be valid JSON
# and it has to agree with the same system the checks above just measured.
snapshot=$("$paddock" report 2>&1)
if printf '%s' "$snapshot" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
  pass "report:json" "parses"
  read -r reported schema live_units live_sites <<< "$(printf '%s' "$snapshot" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d["health"], d["schema_version"], len(d["units"]), len(d["sites"]))')"
  pass "report:schema" "schema_version=$schema"

  # Recompute the rollup here, independently of the Python that produced it,
  # from the report's own raw fields. An earlier version derived it from units
  # alone and so disagreed with reality whenever only a service was down.
  expected=$(printf '%s' "$snapshot" | python3 -c '
import json, sys
d = json.load(sys.stdin)
units, services = d["units"], d["services"]
if next((u for u in units if u["name"] == "paddock.target"), {}).get("state") != "active":
    print("down")
elif (any(not u["ok"] for u in units)
      or any(s["state"] != "active" for s in services)
      or (services and not d["linger"])):
    print("degraded")
else:
    print("ok")')
  check "$([ "$reported" = "$expected" ]; echo $?)" "report:health" \
        "reported $reported, independently computed $expected"

  # Every linked site must appear, and the count must match `paddock sites`.
  listed=$("$paddock" sites | grep -c . || true)
  check "$([ "$listed" = "$live_sites" ]; echo $?)" "report:sites" \
        "$live_sites in report, $listed from paddock sites"
else
  fail "report:json" "$(printf '%s' "$snapshot" | head -c 200)"
fi

echo "== omarchy plugin =="
# The real gate. It reads the manifest only, so a QML fault still needs a live
# shell to surface; tests/test_plugin_manifest.py is the CI-side stand-in.
plugin_dir=$(cd -- "$root/../../plugin" && pwd)
if command -v omarchy >/dev/null 2>&1; then
  if omarchy plugin validate "$plugin_dir" >/dev/null 2>&1; then
    pass "plugin:validate" "$plugin_dir"
  else
    fail "plugin:validate" "$(omarchy plugin validate "$plugin_dir" 2>&1 | head -c 160)"
  fi
  if command -v omarchy-shell >/dev/null 2>&1 && omarchy-shell shell ping >/dev/null 2>&1; then
    id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' \
      "$plugin_dir/manifest.json")
    listed=$(omarchy-shell shell listPlugins 2>/dev/null | python3 -c "
import json, sys
try:
    plugins = json.load(sys.stdin)
except Exception:
    raise SystemExit
for p in plugins:
    if p.get('id') == '$id':
        print('enabled' if p.get('enabled') else 'installed')
")
    check "$([ -n "$listed" ]; echo $?)" "plugin:registered" "${listed:-not found by the shell}"
    # The widget reports its own view of health; it must agree with the CLI.
    # No -q here: that flag suppresses output, which is right for a
    # fire-and-forget refresh and useless when reading a value back.
    widget=$(omarchy-shell "$id" health 2>/dev/null | tr -d '\r\n')
    check "$([ "$widget" = "$reported" ]; echo $?)" "plugin:health" \
          "widget says ${widget:-nothing}, CLI says ${reported:-unknown}"
  else
    pass "plugin:shell" "omarchy-shell not running; skipped"
  fi
else
  pass "plugin:validate" "omarchy not installed; skipped"
fi

echo "== diagnostics =="
if "$paddock" doctor >/dev/null 2>&1; then
  pass "doctor" "all checks pass"
else
  fail "doctor" "$("$paddock" doctor 2>&1 | grep '^FAIL' | tr '\n' ' ')"
fi

echo
if [ "$failures" = 0 ]; then
  echo "acceptance: all checks passed"
else
  echo "acceptance: $failures check(s) failed"
fi
exit $((failures > 0))
