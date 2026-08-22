#!/usr/bin/env bash
# Post-reboot acceptance for a live Paddock installation.
#
# Every defect found in this project so far was found by running against a real
# system while the unit suite stayed green, so this exists to make that run
# repeatable instead of a sequence of hand-typed curl commands. It deploys the
# served fixtures, then checks both systemd managers, the writing contract, the
# sandbox denials, and every configured service.
#
# Read-only apart from the fixture directories it owns under ~/paddock-verify.
# It never uses sudo and never touches a real project.
set -uo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fixtures=${PADDOCK_VERIFY_DIR:-$HOME/paddock-verify}
failures=0

pass() { printf '\033[32mPASS\033[0m  %-28s %s\n' "$1" "${2:-}"; }
fail() { printf '\033[31mFAIL\033[0m  %-28s %s\n' "$1" "${2:-}"; failures=$((failures + 1)); }
check() { if [ "$1" = 0 ]; then pass "$2" "${3:-}"; else fail "$2" "${3:-}"; fi; }

echo "== fixtures =="
for site in alpha beta; do
  directory="$fixtures/$site"
  if [ ! -f "$directory/.paddock.json" ]; then
    fail "fixture:$site" "not linked; see the header of this script"
    continue
  fi
  install -Dm644 "$root/site/public/index.php" "$directory/public/index.php"
  install -Dm644 "$root/site/public/probe.php" "$directory/public/probe.php"
  pass "fixture:$site" "$directory"
done

echo "== system units =="
for unit in paddock.target paddock-dns.service paddock-caddy.service \
            paddock-php@8.4.service paddock-php@8.5.service; do
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
for site in alpha beta; do
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
for site in alpha beta; do
  output=$(curl -fsS --max-time 20 "https://$site.test/probe.php" 2>&1)
  if printf '%s' "$output" | grep -q '^RESULT=PASS$'; then
    pass "probe:$site" "$(printf '%s' "$output" | tr '\n' ' ')"
  else
    fail "probe:$site" "$(printf '%s' "$output" | tr '\n' ' ')"
  fi
done

echo "== supporting services =="
services=$(paddock services 2>/dev/null)
if [ -z "$services" ]; then
  pass "services" "none configured"
else
  # Lingering is what makes a user unit return after a reboot, so a service
  # that is active now proves nothing without it.
  linger=$(loginctl show-user "$(id -u)" --property=Linger --value 2>/dev/null)
  check "$([ "$linger" = yes ]; echo $?)" "services:linger" "$linger"
  while IFS=$'\t' read -r name state address _; do
    [ -n "$name" ] || continue
    check "$([ "$state" = active ]; echo $?)" "service:$name" "$state $address"
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

echo "== diagnostics =="
if paddock doctor >/dev/null 2>&1; then
  pass "doctor" "all checks pass"
else
  fail "doctor" "$(paddock doctor 2>&1 | grep '^FAIL' | tr '\n' ' ')"
fi

echo
if [ "$failures" = 0 ]; then
  echo "acceptance: all checks passed"
else
  echo "acceptance: $failures check(s) failed"
fi
exit $((failures > 0))
