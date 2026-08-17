# State schema version 1

Paddock's first durable schema consists of three strict JSON records. Unknown
fields are rejected so that package upgrades cannot silently misinterpret state.

| Record | Path | Shape |
| --- | --- | --- |
| Settings | `$XDG_CONFIG_HOME/paddock/settings.json` | `schema_version`, nullable `default_php` |
| Sites | `$XDG_CONFIG_HOME/paddock/sites.json` | `schema_version`, site map |
| Runtimes | `$XDG_DATA_HOME/paddock/runtimes.json` | `schema_version`, runtime map |

A site record contains its lowercase name, canonical absolute project root,
selected PHP minor, and TLS state. A runtime record contains its minor version,
absolute activation path, and artifact SHA-256 digest.

Every record is validated before writing. Writes hold a record-specific advisory
lock, create a mode-`0600` candidate in the destination directory, flush it,
atomically rename it, and fsync the directory. Existing records are never
implicitly reset when malformed or from an unsupported schema version.
