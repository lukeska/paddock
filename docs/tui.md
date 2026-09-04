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
with its section name embedded in the top border. Sites shows linked sites, selected PHP and Node versions,
HTTPS state, and the queue and Reverb workers detected for each site.

## Keyboard controls

- `tab` and `shift+tab` switch sections; `1` and `2` jump directly to one.
- On Dashboard, `space` activates the visible Start All or Stop All control. The
  control shows a spinner and the pending action until the operation finishes.
- up/down or `j`/`k` select a site.
- left/right or `h`/`l` select the queue or Reverb worker.
- `space` starts or stops the selected worker.
- `a` toggles its autostart setting.
- `L` opens its recent journal; `j` and `k` scroll and `esc` closes it.
- `/` filters sites by name or path.
- `r` refreshes immediately; the UI also refreshes every five seconds.
- `q` or `ctrl+c` exits.

Successful actions show a confirmation toast on a row below the keyboard
instructions. It disappears automatically after three seconds; a previous
toast's timer never dismisses a newer confirmation.
Background refreshes and in-progress operations are silent so the footer does
not flash or shift vertically.

At widths below 100 columns, the selected site's worker detail moves onto a
second line. The minimum supported terminal size is 48 by 14 cells. Errors
remain visible in the terminal and can be dismissed with `enter`.
