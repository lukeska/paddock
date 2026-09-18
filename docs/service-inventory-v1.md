# Supporting-service inventory schema v1

`paddock services --json` prints one JSON document for scripts. The ordinary
`paddock services` tab-separated display is unchanged.

The root contains `schema_version: 1` and an `instances` array. Every instance
contains exactly:

- `id`, `type`, and user-facing `label`;
- immutable OCI `image` and its display `version` (or `null` for a digest);
- primary host `port` and owned `volume`;
- systemd `state` and boolean `autostart`;
- `connection`, an array of ready-to-copy `KEY=value` lines;
- `addresses`, an array of every `127.0.0.1:HOST → CONTAINER` mapping; and
- `dashboard_url`, or `null` when the service has no web dashboard.

Consumers must reject unsupported schema versions and tolerate new service
types. Field additions or semantic changes require a new schema version.
