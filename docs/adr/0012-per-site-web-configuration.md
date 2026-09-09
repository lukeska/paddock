# ADR 0012: Per-Site nginx Configuration and Repository Trust

- Status: accepted for implementation
- Date: 2026-09-05
- Depends on: [ADR 0011](0011-nginx-http-server.md)
- Experiment: [nginx routing and projection safety](../../experiments/nginx/README.md)

## Context

ADR 0011 moved to nginx partly so a site could carry its own configuration.
Two audiences want that, and they are not the same:

- The person running Paddock wants to add a header, raise a body-size limit, or
  add a health endpoint to one site on their own machine. They may be serving a
  parked folder with no project file at all.
- A team wants the rules a project needs to travel with the project, the same
  way `paddock.yml` already makes PHP and service versions travel.

The second is the problem. `paddock.yml` chooses between options Paddock
defines: a PHP minor, a service version, a project type. An nginx fragment is
not a choice between options — it is arbitrary server configuration, and it
arrives with `git clone`.

nginx runs as the desktop user, not root, so this is not privilege escalation
and ADR 0011's boundary is unaffected. It is still an exposure with no
precedent in Paddock: `location /x { alias /home/you/.ssh; }` in a cloned
repository publishes those files on a `.test` host the user then opens in a
browser holding their session cookies. Nothing else Paddock reads from a
repository can reach outside the project.

## Decision

Two sources, both included at the end of the site's serving `server` block,
the project's first and the user's last.

- `$XDG_CONFIG_HOME/paddock/nginx/<site>.custom.conf` is honoured whenever it
  exists. The person who runs Paddock wrote it; asking them to approve their
  own file would be theatre.
- A project fragment is declared by `nginx:` in `paddock.yml`, as a path
  relative to the project that cannot leave it. Declaring it does nothing. It
  is included only once trusted, and trust is recorded as the fragment's
  SHA-256 in the site record.

Recording the digest rather than a flag means an edit, or a pull that changes
the file, withdraws trust with nobody having to notice. The status a site
reports is derived, never stored: `none`, `missing`, `pending`, `changed`, or
`trusted`.

Refusing is never an error. A site with an untrusted fragment is served
without it, so the safe outcome is also the working one.

## Trust is granted separately, never in passing

`paddock link` and `paddock init` record what a project declares and stop
there. `init` reports an unreviewed fragment with `!`, the marker it already
uses for something a project asked for that Paddock declined to impose, and
exits non-zero.

Trust is `paddock config trust <site>`, or the equivalent control in the GTK
and terminal UIs. Two reasons for a separate act rather than a prompt during
`init`:

- `init` is designed to be scriptable and to distinguish "converged" from
  "converged as far as it could". A blocking prompt in the middle would break
  that, and answering it in a hurry is not review.
- The reviewable thing is the file. A separate command is run after reading it.

## Ordering and precedence

Fragments come last in the block, so a plain directive in one overrides the
generated directive above it — nginx takes the last of two in the same context.

A `location` does not work that way: nginx picks the longest matching prefix
and only then tries regular expressions in the order they appear. A fragment
therefore cannot beat one of Paddock's generated regex locations, such as the
PHP handler, by appearing after it. It can with `^~` or an exact `=` match,
both of which outrank a regex whatever the order. The template
`paddock config edit` writes says so, because the alternative is a rule that is
discovered by having it not work.

## Snapshotted into validated generations

Each included fragment is copied into the candidate generation before
validation. The generated site block records the editable source path in a
comment and includes the immutable copy. A later broken edit therefore cannot
make nginx's last-known-good generation fail during a restart or reboot.

The include is emitted only for a fragment that exists and, for a project
fragment, is trusted. A glob that picked one up whenever it appeared would mean
an unrelated operation's reload failing on an edit nobody had validated —
linking a second site would fail because of a half-finished fragment for the
first. This way the render that notices a fragment is the render that snapshots
and validates it: `nginx -t` reads the copied bytes as part of the candidate,
and a rejection leaves the promoted generation serving and restartable.

Because a fragment lives outside the site registry, nothing in Paddock's state
changes when one is written or edited. `paddock reload`, and the UIs' "Apply
edits", re-render from unchanged state for exactly that case.

## Consequences

- The site record gains an optional `nginx` object of `path` and, once
  trusted, `sha256`. Absent means nothing is declared, so no migration is
  needed and `sites.json` stays at schema 1.
- `paddock.yml` gains `nginx`. An unknown or escaping path is an error, matching
  that file's existing strictness.
- The bridge protocol goes to 2: site payloads carry the project type, document
  root, and both fragments' paths and status, and gain
  `site.set_configuration_trusted`, `site.ensure_configuration`, and
  `web.reload`.
- A UI never writes the template itself. `site.ensure_configuration` creates it,
  so the explanation of nginx's precedence rules has one author.
- `nginx -t` output is shown verbatim on failure. `nginx: [emerg] … in
  /path:12` names the file and the line, and paraphrasing throws that away.
- Trust is per machine, not per repository. Someone who clones the same project
  on a second machine reviews it again there, which is the point.

## Rejected alternatives

- **Honour a project fragment by default.** It is the only thing Paddock would
  read from a repository that can address files outside it.
- **Trust a path rather than its contents.** A pull would then silently change
  what is served, which is the case the digest exists to catch.
- **Restrict fragments to an allowlist of directives.** The value of nginx is
  that a user's existing snippets work. An allowlist would have to grow to
  contain every one of them, and `alias`, `root`, and `proxy_pass` — the risky
  three — are also among the most useful.
- **A single fragment inside the project only.** It would not serve a parked
  folder with no project file, which is a first-class way to use Paddock.
- **Copy fragment contents into the generated site file.** Validation would
  still work, but every error message would point at a generated file the user
  did not write.
