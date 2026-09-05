# Parking folders

Paddock creates and parks `~/Paddock` during setup or first use. Every visible
immediate subdirectory becomes `<folder-name>.test`; nested directories do not
become separate sites. Additional folders can be managed from the Sites page or
with the CLI:

```bash
paddock park ~/Code
paddock paths
paddock forget ~/Code
```

New parked sites start on the configured default PHP version and HTTP. Their PHP
and HTTPS settings can then be changed independently from the Sites page.
Removing a parking folder never deletes it or its projects.

Explicit links take precedence over a parked folder with the same site name. If
two parked folders contain the same child name, Paddock serves neither candidate
until the collision is resolved; it never guesses based on path order.

A parked folder is identified the same way an explicitly linked project is,
so a parked WordPress or static folder is served with its own rules rather
than Laravel's. Reconciliation runs on a filesystem event and must not fail,
so a folder that is mid-clone falls back to its type's default document root
instead of raising.

Paddock generates user-level systemd path units for registered folders. Creating,
renaming, or removing an immediate child triggers a reconciliation and web
server reload even when the native application is closed.
