# State schema version 1

Paddock's first durable schema consists of strict JSON records. Unknown
fields are rejected so that package upgrades cannot silently misinterpret state.

| Record | Path | Shape |
| --- | --- | --- |
| Settings | `$XDG_CONFIG_HOME/paddock/settings.json` | `schema_version`, nullable `default_php` and `default_node`, initial-setup flags, optional `service_labels` map |
| Sites | `$XDG_CONFIG_HOME/paddock/sites.json` | `schema_version`, site map |
| PHP runtimes | `$XDG_DATA_HOME/paddock/runtimes.json` | `schema_version`, PHP runtime map |
| Node runtimes | `$XDG_DATA_HOME/paddock/node-runtimes.json` | `schema_version`, Node runtime map |
| Services | `$XDG_CONFIG_HOME/paddock/services.json` | `schema_version`, service map |
| Parking | `$XDG_CONFIG_HOME/paddock/parking.json` | `schema_version`, absolute parked-path array |

A site record contains its lowercase name, canonical absolute project root,
selected PHP minor, optional Node major, and TLS state. A PHP runtime record
contains its minor version, while a Node runtime record contains its major;
both contain an absolute activation path and artifact SHA-256 digest. A service record
contains its lowercase name, registry-qualified container image, published
loopback port, and data volume name.

Site records may carry `origin: "parked"` and an absolute `parking_path`.
Records without `origin` are explicit links. This lets reconciliation prune
folders that disappeared without ever deleting a site the user linked directly.
An enabled site Reverb worker is represented by a `reverb` object containing
its allocated loopback `port`; runtime and autostart state remain owned by
systemd rather than duplicated in JSON.
A configured Laravel queue worker is represented by a `queue` worker record;
its running and autostart state likewise remains owned by systemd.

`service_labels` is UI-only metadata keyed by stable catalog service name. Old
version-1 settings records without it normalize to an empty map when read.

Every record is validated before writing. Writes hold a record-specific advisory
lock, create a mode-`0600` candidate in the destination directory, flush it,
atomically rename it, and fsync the directory. Existing records are never
implicitly reset when malformed or from an unsupported schema version.
