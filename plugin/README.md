# Paddock status — Omarchy shell plugin

An optional companion to the `paddock` CLI. It presents state and owns none:
every fact comes from `paddock report`, and nothing here starts, stops, or
configures anything. ADR 0008 fixes that boundary; ADR 0010 covers the
services it displays.

## What it shows

A dot in the bar coloured by overall health — accent for `ok`, urgent for
`degraded` or `down`, muted for `unknown` — with a tooltip summary. Clicking
opens a panel with four sections:

- **Status** — every unit the installation owns, including both PHP-FPM
  masters and the route-only DNS unit.
- **PHP** — installed minors, their release, and which is the default.
- **Services** — name, state, and loopback address, plus a warning when
  lingering is disabled, because those services stop at logout.
- **Sites** — host, PHP minor, whether TLS is on, and a button that opens the
  site in your browser.

`doctor` and `logs` open a floating terminal. Nothing else acts.

## How it is built

| File | Role |
| --- | --- |
| `manifest.json` | declares `service` and `bar-widget` kinds |
| `Service.qml` | the only thing that runs `paddock report` |
| `BarWidget.qml` | the dot, and the host for the panel |
| `Panel.qml` | the four sections |

Polling lives in the **service**, not the widget. A bar widget is instantiated
once per monitor, so a `Timer` there would multiply the subprocess count by the
number of screens. The widget reaches the singleton with
`bar.shell.serviceFor("dev.paddock.status")`; bar widgets are not handed a
`service` property, only panel entries are.

Enabling the widget into a bar section enables the service too, because
`PluginRegistry.isEnabled` searches `bar.layout` before `plugins[]`.

The panel is a nested `Loader` rather than a `panel` kind, so it needs no
second enable step. `Panel.qml` sets `manageIpc: false`: the shell refuses a
second handler for a target, and `Service.qml` already owns
`dev.paddock.status`. Summoning is routed through the widget's
`open`/`close`/`opened`, which is what `Bar.findPanelWidget` looks for.

Colours bind to the `Color` and `Style` singletons from `qs.Commons`, so a
theme change re-themes the plugin live with no code and no literals to drift.

## Installing

Supported lifecycle, per ADR 0008:

```bash
omarchy plugin add <git-url> --enable --yes
omarchy plugin enable dev.paddock.status --section right
```

For development from this repository:

```bash
./scripts/plugin-dev-install.sh          # copies into ~/.config/omarchy/plugins
omarchy plugin enable dev.paddock.status --section right
```

It copies rather than symlinks: the registry rejects a symlink anywhere inside
a plugin folder.

## Settings

One, editable per bar entry in `~/.config/omarchy/shell.json`:

```bash
omarchy bar set dev.paddock.status refreshIntervalSec 30
```

Default 15 seconds, range 5–300. The widget forwards it to the service, which
holds no settings of its own.

## Refreshing and reloading

```bash
omarchy-shell dev.paddock.status refresh    # force a poll
omarchy-shell dev.paddock.status health     # ok | degraded | down | unknown
```

Do not pass `-q` when you want a value back; it suppresses output and is for
fire-and-forget calls only.

Editing files under `~/.config/omarchy/plugins/` hot-reloads the code. **A
reload does not always re-instantiate an already-mounted bar widget**, so
adding or renaming a member the shell looks for — `open`, `close`, `opened` —
can leave the old instance in the slot. The symptom is
`summon: no live bar widget for: dev.paddock.status` while the widget is still
drawn. Restart the shell when the widget's interface changes:

```bash
omarchy restart shell
```

Internal changes, including anything inside `Panel.qml`, hot-reload fine.

## When Paddock is absent

A missing `paddock`, a non-zero exit, or unparseable output leaves the dot
muted and the panel explaining why, while the last good snapshot is retained
rather than blanked. The shell stays usable without Paddock, and the CLI stays
fully usable without the shell.

## Debugging

The shell logs to `/run/user/$UID/quickshell/by-id/*/log.qslog`; read it with
`qs log <path>` — without `-f`, which follows. `omarchy plugin validate
<dir>` checks the manifest only, so a QML fault needs a live shell to surface.
