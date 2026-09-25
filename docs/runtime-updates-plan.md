# Independently refreshed PHP and Node runtime catalogs

Status: proposed. Implement one unit at a time; this document is the reference
for scope and acceptance, not a claim that the feature already exists.

## Goal and current behavior

Paddock should discover new PHP and Node patch releases without requiring a new
Paddock package, tell the user exactly what is installed and available, and
update a selected minor/major only on request. A site's PHP minor (for example,
8.4) and Node major (for example, 24) must not change during a patch update.

Today `resources/artifacts.json` and `resources/node-artifacts.json` are shipped
with the package. The TUI marks an installed minor/major as simply installed
and displays the catalog's patch number even when the installed patch is older.
The PHP and Node installers already pin archive SHA-256 values and retain
release directories, but their activation and registry writes are not a fully
transactional upgrade/rollback operation.

Non-goals for this feature: automatic runtime installation, automatic site
major/minor changes, automatic asset rebuilds, a Paddock package updater, and
updating Composer or the Laravel installer.

## Security and product decisions

- A network-fetched catalog changes the trust boundary in ADR 0009. Before
  enabling refresh, amend that ADR: the package ships the catalog-verification
  key; a detached signature authenticates catalog bytes; the catalog pins each
  archive hash; the archive hash is checked before extraction.
- Use the existing Paddock release signing identity, with its public key
  packaged as a file. Verify in an isolated GPG keyring, never against the
  user's default keyring or web-of-trust settings. Add `gnupg` as a package
  dependency if the verifier invokes `gpg`. Require a valid signature by the
  pinned primary fingerprint; fail closed on missing, expired, revoked, or
  malformed signatures. Do not put a private key in CI.
- Publish `php.json`, `php.json.sig`, `node.json`, and `node.json.sig` at stable
  HTTPS URLs under a dedicated catalog publishing surface. The exact host and
  deployment mechanism are a Unit 1 decision, but must be recorded in code
  and documentation before implementing the fetcher. Keep immutable archived
  copies of every published catalog revision for audit and recovery. Never
  replace an archive URL already referenced by a published catalog.
- Each signed catalog has `schema_version`, a monotonic integer `revision`,
  and an `artifacts` array. Persist the highest accepted revision per catalog
  outside the replaceable cache. Reject lower revisions, including correctly
  signed ones. Equal revisions must have identical bytes; a changed document
  under the same revision is an error. An ordinary refresh never downgrades.
- Keep packaged catalogs as bootstrap/offline fallback. A successfully
  refreshed catalog is selected ahead of the packaged one. A user-supplied
  PHP catalog remains an explicit override and is never overwritten by
  refresh; provide equivalent Node override behavior. The UI must identify
  which catalog is effective and whether it is stale. A bad refresh leaves
  the last trusted catalog intact and produces a visible error.
- No runtime update occurs merely because the TUI opens or refreshes metadata.
  Only an explicit Install, Update, or Rollback action changes a runtime.

## Implementation units

### Unit 1 — Catalog format, publication contract, and ADR

1. Choose and document the stable HTTPS distribution location and how signed
   catalog files are promoted there without mutating archived revisions.
2. Define strict PHP/Node catalog schemas, revision rules, architecture rules,
   and artifact URL constraints. Reuse the existing artifact fields and parser
   validation where possible.
3. Amend ADR 0009 and the release playbook for the new trust chain, key
   rotation, revocation, catalog retention, and recovery from bad publication.
4. Add local payload fixtures for valid, invalid, and older revisions.
   Test-only signed fixtures are added with signature verification in Unit 2.
   Do not enable fetching in this unit.

Done when the format and signing/publication procedure are unambiguous and
schema tests pass without network access.

### Unit 2 — Trusted refresh and cached catalog selection

1. Implement signature verification with a package-owned public key and an
   isolated temporary GPG home. Pin the full primary fingerprint, not a short
   key ID. Do not trust a key or signature downloaded alongside the catalog.
2. Fetch catalog and signature over HTTPS with timeouts and size limits;
   validate signature, schema, URLs, and revision before atomic replacement.
   Protect concurrent refreshes with a lock and fsync the committed files.
3. Store accepted revision and digest in durable user state. If either cached
   catalog fails validation later, report it and use the bundled fallback;
   do not silently treat the corrupt cache as an update source.
4. Add `paddock runtimes refresh` and a read-only `paddock runtimes status`
   showing source, revision, last successful refresh, and any error. Refresh
   both catalogs independently and report partial success clearly.
5. Make the CLI, controller, and TUI snapshot all use the same effective
   catalog resolver; remove direct packaged-path lookups from individual
   install paths. Preserve `paddock php catalog PATH` override semantics and
   add a matching Node override command.

Done when valid refreshes survive restart, offline use works, and tampered,
expired-key, replayed, interrupted, and concurrent refreshes cannot replace a
trusted catalog. Tests use local fixtures and fake HTTP; normal CI stays
network-free.

### Unit 3 — Accurate installed-patch inventory

1. Store exact patch release and artifact hash in PHP and Node registry
   records. Migrate existing records without deleting runtimes: inspect the
   installed executable and reconcile its release directory/hash. If that
   cannot be established, show an unknown installed patch, not the catalog's
   patch number.
2. Extend version snapshots with installed release, available release,
   `update_available`, catalog source/staleness, and previous release where
   known. Compare semantic numeric versions within one minor/major; never
   present an older catalog entry as an update.
3. Add tests for fresh, current, older, locally registered, missing, and
   legacy-schema runtimes. Preserve the existing per-site selection format.

Done when CLI/controller snapshots report installed and available patches
truthfully, without changing any runtime or site.

### Unit 4 — Transactional patch update and rollback

1. Make `paddock php install 8.4` and `paddock node install 24` no-ops when
   the selected catalog patch is already active; offer the newer patch when
   available. Reject silent downgrades and do not overwrite a custom runtime
   without explicit confirmation.
2. Stage and validate the new archive before changing active links or registry
   state. PHP validation includes CLI version, FPM version/configuration,
   required extensions/functions, and a healthy FPM service after restart.
3. On any activation failure, restore the previous symlink, FPM config,
   registry entry, and service; surface both the original failure and any
   rollback failure. Node performs the analogous link/registry transaction.
4. Retain the previous release. Add `paddock php rollback MINOR` and
   `paddock node rollback MAJOR` for that retained patch, with the same
   validation and failure recovery. Do not garbage-collect older releases in
   this feature.
5. Keep sites bound to their selected minor/major. Document that Node asset
   builds may need rerunning after an update; do not rebuild projects
   automatically.

Done when fault-injection tests at every switch/restart/write boundary leave
either the old or the fully validated new runtime active, never a mixed state.

### Unit 5 — TUI discovery and update controls

1. Extend the bridge protocol and Go backend types with the Unit 3 inventory
   fields and a catalog-refresh operation. Update both protocol versions
   together.
2. On PHP and Node tabs, show Installed, Available, and Status separately:
   `Not installed`, `Current`, `Update available`, or `Catalog unavailable`.
   Render `[ Install ]` or `[ Update ]` only when the action is valid.
3. Refresh metadata asynchronously on opening the TUI only if the last
   successful check is older than 24 hours. Never block initial rendering or
   let a network failure erase installed-runtime information. Add a manual
   Refresh action and show source, checked time, and stale/error state.
4. Enter on Install/Update shows the exact old → new patch and asks for
   confirmation. Show a spinner while downloading/validating/activating;
   disable duplicate actions. Offer Rollback only where a retained previous
   release is known. Successful actions use the existing temporary toast;
   errors remain readable and include recovery information.
5. Add TUI tests for installed/current/update/offline/error/rollback states,
   confirmation, cancellation, spinner, and snapshot refresh.

Done when users can distinguish a newer patch from an uninstalled runtime and
can update or roll back without changing their site's selected minor/major.

### Unit 6 — Promotion tooling, documentation, and acceptance

1. Add a maintainer command to validate URLs, hashes, architecture coverage,
   and provenance of candidate artifacts; increment revision; produce the
   exact catalog bytes for offline signing; verify the detached signature;
   then publish without replacing an existing revision.
2. Document `runtimes refresh/status`, PHP/Node update and rollback, offline
   behavior, trust/key rotation, and the distinction between a Paddock package
   update and a runtime patch update.
3. In an Omarchy VM, test: fresh install with bundled catalogs; valid refresh;
   offline refresh; PHP patch update while two sites share a minor; CLI/FPM
   agreement; Node patch update and asset rebuild; rollback of each; a
   deliberately broken PHP candidate; TUI status and error presentation.

Done when the signed catalog can be promoted independently of an application
tag and the full manual scenario passes. No automatically scheduled runtime
installation is added.

## Delivery order

Units 1–3 can ship as read-only discovery. Unit 4 enables patch changes only
after its transactional tests pass. Unit 5 exposes those operations in the
TUI. Unit 6 completes the independent publication path and VM acceptance;
remote refresh must remain disabled by default until the signed publication
surface and key material from Units 1, 2, and 6 are ready.
