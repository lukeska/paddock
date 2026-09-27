# VM runtime patch-upgrade fixture

This fixture is for a disposable x86_64 Omarchy VM. It installs PHP 8.5.8 and
Node 24.19.0 through Paddock's normal installer, then lets the signed revision-1
catalogs offer PHP 8.5.10 and Node 24.20.0. It never changes the production
catalogs or Paddock's no-downgrade rule. Do not use it on a machine with
projects that depend on the installed runtimes.

The old public Paddock PHP 8.5.8 archive is **not** used: it failed the
`mb_split` check. Use a local build made with the current recipe. The fixture
validates its CLI version, `mb_split`, and FPM version before packaging it and
pins the copied archive's SHA-256. Node uses the [official 24.19.0 archive and
checksum](https://nodejs.org/download/release/v24.19.0/SHASUMS256.txt).

On the host, from the Paddock repository:

```bash
# Only needed if the validated old archive is not already present:
./tests/runtime-upgrade/build-old-php.sh

python tests/runtime-upgrade/fixture.py prepare \
  --php-archive release/dist-fixture/paddock-php-8.5.8-linux-x86_64.tar.gz \
  --output /tmp/paddock-runtime-upgrade
omavm push /tmp/paddock-runtime-upgrade /tmp/
```

If a previously validated local archive is already available, pass that path
to `--php-archive` instead. `prepare` refuses a non-empty output directory;
choose a new output path for another bundle.

For the cleanest first-setup test, install the Paddock package in a fresh VM
but **do not run `paddock setup` yet**. As the desktop user `lukeska`:

```bash
python /tmp/paddock-runtime-upgrade/fixture.py stage
paddock runtimes status            # both should say local override
paddock setup --yes
python /tmp/paddock-runtime-upgrade/fixture.py unstage
python /tmp/paddock-runtime-upgrade/fixture.py check --php 8.5.8 --node 24.19.0
paddock runtimes refresh
paddock runtimes status            # both should say refreshed, revision=1
```

The one-command public installer runs `paddock setup` automatically, so use a
manual package install for this first-setup path. On an already set-up VM,
`seed` installs the older versions only when PHP 8.5 and Node 24 are absent:

```bash
python /tmp/paddock-runtime-upgrade/fixture.py seed
```

`seed` refuses to overwrite or delete a current runtime. If the VM already
has PHP 8.5.10 or Node 24.20.0, start from a clean VM snapshot or explicitly
remove those runtimes with `paddock php remove 8.5` and
`paddock node remove 24` after checking that no site uses them. Both `seed`
and `unstage` remove only fixture-owned local catalog overrides.

Now open the PHP and Node TUI tabs. They should show the installed older patch
and available newer patch. Update both through the TUI, then verify:

```bash
python /tmp/paddock-runtime-upgrade/fixture.py check --php 8.5.10 --node 24.20.0
paddock php list
paddock node list
```

Check a PHP site over HTTPS and run its CLI smoke command. To repeat the
upgrade without reinstalling the fixture, run `paddock php rollback 8.5` and
`paddock node rollback 24`, verify the older versions with `fixture.py check`,
then update again through the TUI. A local catalog override left in place
would hide the signed catalog, so `check` also asserts that neither override
exists.
