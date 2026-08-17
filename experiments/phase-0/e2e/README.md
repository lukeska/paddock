# Experiment 0.13: End-to-End Rehearsal

## Status

- State: passed
- Date completed: 2026-08-17
- Passes required: initial, targeted restart, reboot
- Cleanup: exact setup-owned units, NetworkManager connection, config, helpers,
  state directory, and temporary source runtimes

The combined probe uses the retained Paddock CA but creates a dedicated leaf
certificate and removes it with the probe state. Final teardown also removes the
experiment-created CA trust to restore the original pre-Phase-0 machine state.

All three passes succeeded. Setup used boot ID
`d77ea9c4-7b29-4d57-982d-c90b8def358b`; the reboot pass used boot ID
`bdff7461-3773-475a-8119-ceb28498687c`. Cleanup was audited after removal, and
the CA fingerprint was absent from both system and user NSS trust stores.

## Commands

```bash
sudo PADDOCK_USER="$USER" \
  RUNTIME_SOURCE=/tmp/paddock-e2e-runtimes.un67xK \
  ./experiments/phase-0/e2e/setup.sh

./experiments/phase-0/e2e/smoke.sh

sudo PADDOCK_USER="$USER" \
  ./experiments/phase-0/e2e/restart-and-smoke.sh

# Run after the reboot pass.
sudo PADDOCK_USER="$USER" ./experiments/phase-0/e2e/remove.sh
```
