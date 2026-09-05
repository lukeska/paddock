# Terminal UI

`paddock tui` opens a full-screen Bubble Tea client that uses the same Python
application controller as the GTK app. It does not duplicate state or invoke
Paddock's human-readable CLI output. A private, versioned NDJSON bridge keeps
the UI process isolated from systemd and Paddock's state files.

Colors come from Omarchy's active, normalized `colors.toml`, using the same
validated palette adapter as Paddock's GTK app. The next periodic refresh
applies a newly selected Omarchy theme without restarting the TUI. If that
palette is unavailable, Paddock falls back to the terminal's detected light or
dark appearance.

The Dashboard shows the current state of Paddock's web stack and supporting
service instances. Each dashboard section uses a responsive bordered fieldset
with its section name embedded in the top border. Sites presents a table with
name, PHP, Node, HTTP/HTTPS lock state, and an Open link. A selected row opens
an actionable detail view with the same configuration, launch, and worker
controls as the desktop UI.
The search input remains visible above the table and can be focused with `/` or
a mouse click.

## Keyboard controls

- `tab` and `shift+tab` switch sections; `1` and `2` jump directly to one.
- On Dashboard, `space` activates the visible Start All or Stop All control. The
  control shows a spinner and the pending action until the operation finishes.
- up/down or `j`/`k` select a site.
- `enter` opens the selected site's detail view; `esc` returns to the table.
- `o` opens the selected site in the system browser.
- With mouse reporting available, clicking a row opens its detail view and
  clicking its Open link launches the site directly.
- `/` filters sites by name or path.
- In site details, up/down selects an action and `enter` activates it. Left/right
  cycles installed PHP or Node versions when the corresponding row is selected.
- Detail actions toggle HTTP/HTTPS, open the URL or project path, launch a
  terminal or Zed, and control queue/Reverb state, autostart, and logs.
- `r` refreshes immediately; the UI also refreshes every five seconds.
- `q` or `ctrl+c` exits.

Successful actions show a confirmation toast on a row below the keyboard
instructions. It disappears automatically after three seconds; a previous
toast's timer never dismisses a newer confirmation.
Background refreshes and in-progress operations are silent so the footer does
not flash or shift vertically.

At 80 columns and wider, site Details and Workers are shown side by side; on
narrower terminals they stack vertically. The minimum supported terminal size
is 48 by 14 cells. Errors remain visible and can be dismissed with `enter`.

## Acceptance coverage

The package test suite builds the real Go executable and drives it inside a
Linux pseudo-terminal against a deterministic NDJSON backend. It verifies real
key input and rendered output for Sites-to-details navigation, returning to the
table, clean exit, and dashboard spinner/toast behavior. Model and bridge unit
tests provide the more exhaustive state and action coverage.
