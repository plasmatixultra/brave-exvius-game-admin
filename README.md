# FFBE Game Admin

A local web page for your own **Exvius Revival** save: grant resources, build and awaken
units, and replay story checkpoints and side content — with a preview of exactly what will
change before anything is written.

It runs on the machine that already runs the game's Docker container, finds that container,
its save and its backups by itself, and needs nothing but Python and Docker.

![Progress: story checkpoints and side content](docs/screenshots/progress.png)

| | |
|---|---|
| ![Quick Grants](docs/screenshots/grants.png) | ![Unit Builder](docs/screenshots/units.png) |

**Contents:** [Quick start](#quick-start) · [If the game runs on another machine](#if-the-game-runs-on-another-machine)
· [The views](#the-views) · [How it touches your save](#how-it-touches-your-save)
· [If something goes wrong](#if-something-goes-wrong) · [Requirements](#requirements)
· [What it does not do](#what-it-does-not-do) · [Uninstall and rollback](#uninstall-and-rollback)
· [Development](#development)

## Quick start

```bash
# 1. On the machine that runs the game:
git clone https://github.com/plasmatixultra/brave-exvius-game-admin
cd brave-exvius-game-admin

# 2. Start it (Python 3.12 or newer, standard library only):
python3 app.py

# 3. Open the page it prints:
#    http://127.0.0.1:8750
```

That is the whole install. There is nothing to configure in the normal case: the page reads
the container's own mounts with `docker inspect`, finds the save and the backups, and copies
the engine's source out of the image once (~80 MB, under a second) so previews are fast.

The page checks itself before it offers anything. If it cannot find the game it says what it
looked for and refuses to apply anything until that is fixed — see
[If something goes wrong](#if-something-goes-wrong).

It looks for a container called `ffbe` (the name the Revival compose file uses). If yours is
called something else — or Docker's daemon is not reachable from the page — copy
`admin.toml.example` to `admin.toml` and set `container = "<its name>"`. The two directories
in that file are only needed when Docker cannot be asked at all.

## If the game runs on another machine

The app drives **that machine's** Docker daemon and mounts its paths, so it is installed and
started there — on the NAS, the server, the mini-PC — not on your laptop. Your laptop then
opens it over the network:

```bash
# on the box with the game:
ADMIN_BIND=192.168.1.20 python3 app.py     # its address, not 0.0.0.0
# on your laptop:
http://192.168.1.20:8750
```

There is **no password**. Bind it to an address only your own devices can reach — a VPN or
tailnet address is the right choice, plain `0.0.0.0` on shared wifi is not. The page warns
you when it is exposed, and `127.0.0.1` (the default) is what you want if you only ever open
it from that machine.

Running the app on your laptop with the game elsewhere is not supported yet: two of the
containers it starts mount *local* directories, so the daemon has to be the local one. An ssh
tunnel is the closest you can get today:

```bash
ssh -L 8750:127.0.0.1:8750 you@the-game-box     # then http://127.0.0.1:8750
```

## The views

| View | What it changes |
|---|---|
| **Quick Grants** | Gil, Lapis, unit EXP points, tickets, vision cards, and new units at the form you pick |
| **Unit Builder** | Level, awakening stars, LB level, EX level, pots, doors, trust and super-trust |
| **Awakening Kit** | Everything one awakening needs, in one apply, in the right order |
| **Ability Awakening** | An ability to a target tier, chaining the lower tiers it needs |
| **Vision Cards** | Card level and total EXP |
| **Progress** | Story checkpoints (Lapis, gil, missions, quests, esper trials, roster rewards) and side content per world |
| **Log** | Every apply, in order, with what it changed |

Every write is a preview first: the page shows the exact currency deltas, roster additions
and rewards, and you press Apply. The numbers come from the game's own code — the same
functions the server uses — so caps, ticket types and quest rules are the game's, not a
guess.

## How it touches your save

The game loads the player **once**, at startup, and writes it back from memory, so the page
never edits a save that is being read:

1. stop the game container
2. take a timestamped backup under `server/state/backups/`
3. edit a **copy** with the engine's own code, running inside the game image
4. validate the copy against the backup and the expected deltas
5. replace the live file with an atomic `mv`
6. start the container and poll until it reports the new save

If any step fails after the stop, the backup is restored and the container is started again.
If the page itself dies mid-apply, the next start finds a marker and finishes the repair
before doing anything else. A rollback is never reported as a success.

The page runs on the host but never writes the save: every file operation happens in a
throwaway container that mounts the game's own directories. That is deliberate — the
container writes those files as root, so doing the same work from the host would need sudo on
Linux and would behave differently on a NAS or Docker Desktop. **No sudo, no ssh, no root on
the host.**

The game is disconnected for roughly twenty seconds per apply.

## If something goes wrong

The page says what it found before you can apply anything. The common messages:

| The page says | What it means |
|---|---|
| *container ffbe was not found…* | the game container is not running, or has another name — set `container` in `admin.toml` |
| *Set container, server_dir and data_dir in admin.toml* | Docker could not be asked at all; fill in the two host paths |
| *this engine cannot run the agent: missing …* | your image has moved on from the one this was built against; it names the symbol and stops before touching anything |
| *this page is reachable from other machines* | `ADMIN_BIND` is not loopback, and there is no password |
| *another apply is already running* | one apply at a time, by design — wait for the first to finish |

Every apply leaves a backup named `campaign-<UTC>-<pid>.json`. To undo one by hand, stop the
container and copy that file over `server/state/campaign.json` (as root, or through a
container — the game owns those files).

## Requirements

- **Python 3.12+** (standard library only: `tomllib`, `http.server`)
- **Docker**, with the game's container running or present
- The `ffbe-ios-server:local` image from
  [Exvius-revival-ios](https://github.com/CreamySeaman6/Exvius-revival-ios)
- Your user must be able to run `docker` (usually membership of the `docker` group)

Tested on Linux against `ffbe-ios-server:local`, with a real save, including a killed apply,
a refused apply and a rollback. The app checks the image for the engine symbols it needs and
refuses to apply — before stopping anything — if they have moved.

**Untested so far:** Windows and macOS, rootless Docker, and engines other than the image
this was built against. The file operations all happen inside containers and the lock is
portable, but that is a design argument, not evidence.

## What it does not do

- No accounts, no authentication, no password. It binds `127.0.0.1` unless you say otherwise,
  and the page tells you when it is reachable from elsewhere.
- It does not change the game's code, and it does not bypass the engine.
- It does not touch anything else in your Docker setup — no new services, no compose edits,
  no socket mounts.
- It does not run on your laptop while the game runs elsewhere (see above).

## Uninstall and rollback

- **Stop it:** Ctrl-C, or kill the `python3 app.py` process. Nothing else is left running.
- **Remove it:** delete the folder. The only other thing it creates is
  `~/.cache/ffbe-admin` (the engine export and a copy of the save for reads); delete that too
  and the next install rebuilds it.
- **Undo a change:** every apply left a backup — see [If something goes wrong](#if-something-goes-wrong).
- **Port clash:** `ADMIN_PORT=9000 python3 app.py`.

## Development

No dependencies, no build step, no virtualenv:

```bash
python3 -m unittest tests.test_app tests.test_pipeline tests.test_parity tests.test_cache \
    tests.test_config tests.test_transport tests.test_applysave tests.test_engine \
    tests.test_compat tests.test_packaging      # no game required
bash tests/test_apply_files_sh.sh               # the in-container file worker
sh tests/run_in_container.sh                    # the agent suite, needs the game image
node tests/ui_check.mjs http://127.0.0.1:8750   # the page itself, needs a live stack
```

Layout: `app.py` (HTTP and the page's API), `applysave.py` (the write sequence),
`transport.py` (the docker CLI), `config.py` (where the game lives), `engine.py` (the engine
export), `compat.py` (drift guard), `localdata.py` (read cache), `agent/` (the operations,
run inside the image and on the host), `runner/apply-files.sh` (the in-container file worker),
`static/` (the page).

The build record — designs, plans, acceptance evidence — lives in `docs/`.

## Links

- Game server: [CreamySeaman6/Exvius-revival-ios](https://github.com/CreamySeaman6/Exvius-revival-ios)
- Android side: [Elvlin/exvius-offline](https://github.com/Elvlin/exvius-offline)

## Disclaimer

This is an unofficial fan tool. It edits **your own** save file and is not affiliated with or
endorsed by Square Enix, Gumi, or the Revival projects. The game, its data tables and its art
belong to their owners; this repository contains none of the game's binaries. Keep the
backups it makes. Use at your own risk — see [LICENSE](LICENSE).

## Licence

MIT — see [LICENSE](LICENSE).
