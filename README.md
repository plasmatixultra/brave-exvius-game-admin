# FFBE Game Admin

A local web page for your own **Exvius Revival** save: grant resources, build and awaken
units, and replay story checkpoints and side content — with a preview of what will change
before anything is written.

It runs on the machine that already runs the game's Docker container, and finds that
container, its save and its backups by itself. Python 3.11+ and Docker are the only
prerequisites; nothing else is installed.

![Progress: story checkpoints and side content](docs/screenshots/progress.png)

| | |
|---|---|
| ![Quick Grants](docs/screenshots/grants.png) | ![Unit Builder](docs/screenshots/units.png) |

## Quick start

```bash
# 1. On the machine that runs the game: copy this folder across
#    (or `git clone` it once the repository is published):
cd ffbe-game-admin

# 2. Start it (Python 3.11 or newer, standard library only):
python3 app.py

# 3. Open the page it prints:
#    http://127.0.0.1:8750
```

The page checks itself first. If it cannot find the game it says so — naming the container
it looked for and the paths it would use — and refuses to apply anything until that is
fixed. In the normal case there is nothing to configure.

It looks for a container called `ffbe` (the name the Revival compose file uses) and reads
its mounts with `docker inspect`. If yours is called something else — or Docker's daemon is
not reachable from the page — copy `admin.toml.example` to `admin.toml` and set
`container = "<its name>"`; the two directories are only needed when Docker cannot be asked
at all.

## What it can do

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
and rewards before you press Apply.

## How it touches your save

The game loads the player **once**, at startup, and writes it back from memory. So the
page never edits a save that is being read:

1. stop the game container
2. take a timestamped backup under `server/state/backups/`
3. edit a **copy** with the engine's own code, running inside the game image
4. validate the copy against the backup and the expected deltas
5. replace the live file with an atomic `mv`
6. start the container and poll until it reports the new save

If any step fails after the stop, the backup is restored and the container is started
again. If the page itself dies mid-apply, the next start finds a marker and finishes the
restore before doing anything else. A rollback is never reported as a success.

The page runs on the host but never writes the save: every file operation happens in a
throwaway container that mounts the game's own directories. That is deliberate — the
container writes those files as root, so doing the same work from the host would need sudo
on Linux and would behave differently on a NAS or Docker Desktop. **No sudo, no ssh, no
root on the host.**

The game is disconnected for roughly twenty seconds per apply.

## What it does not do

- No accounts, no authentication, no remote access. It binds `127.0.0.1`; if you need to
  reach it from another device, put your own tunnel or reverse proxy in front of it.
- It does not change the game's code, and it does not bypass the engine: every edit is made
  by the engine's own functions, so caps, ticket types and quest rules are the game's.
- It does not touch anything else in your Docker setup.

## Requirements

- Python 3.12+ (standard library only: `tomllib`, `http.server`)
- Docker, with the game's container running or present
- The `ffbe-ios-server:local` image from
  [Exvius-revival-ios](https://github.com/CreamySeaman6/Exvius-revival-ios)

Tested against `ffbe-ios-server:local`; the app checks the image for the engine symbols it
needs and refuses to apply — before stopping anything — if they have moved.

## Links

- Game server: [CreamySeaman6/Exvius-revival-ios](https://github.com/CreamySeaman6/Exvius-revival-ios)
- Android side: [Elvlin/exvius-offline](https://github.com/Elvlin/exvius-offline)

## Disclaimer

This is an unofficial fan tool. It edits **your own** save file and is not affiliated with
or endorsed by Square Enix, Gumi, or the Revival projects. Keep the backups it makes; if a
save ever looks wrong, stop the container and copy a backup back over
`server/state/campaign.json` by hand. Use at your own risk.

## Licence

MIT — see [LICENSE](LICENSE).
