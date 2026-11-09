# OmaPoker

Texas Hold'em for **Omarchy** systems. Play against bots, pass one laptop
around the table with friends, or host a code-gated game on your LAN — with a
teaching dial that explains as much or as little as you want.

```
   ____                 ____       _
  / __ \____ ___  ____ |  _ \ ___ | | _____ _ __
 / / / / __ `__ \/ __ \| |_) / _ \| |/ / _ \ '__|
/ /_/ / / / / / / /_/ /|  __/ (_) |   <  __/ |
\____/_/ /_/ /_/\__,_/ |_|   \___/|_|\_\___|_|
```

## Requirements

OmaPoker refuses to run on anything that is not Omarchy. On launch it verifies:

1. `/etc/os-release` reports `ID=omarchy`
2. the Omarchy install tree exists (`/usr/share/omarchy`)
3. the `omarchy` command is on `PATH`
4. an Omarchy version is readable

If any check fails you get a branded refusal screen, a desktop notification, and
exit code 3.

> **Honest note:** this is a firm check, not copy protection. It stops a normal
> user on Ubuntu, Fedora, macOS or Windows/WSL. It cannot stop a determined
> owner from editing a script that runs on their own machine, and nothing here
> pretends otherwise.

## Install

```bash
./scripts/install.sh
```

That creates a managed virtualenv at `~/.local/share/omapoker/venv`, installs
OmaPoker into it (editable, so edits to this checkout take effect immediately),
and drops a launcher at `~/.local/bin/omapoker` plus an icon and a desktop entry.
Re-running it is safe. Nothing is written outside `$HOME`, so no root is needed.

Run it:

```bash
omapoker            # play
omapoker --check    # verify the gate, print the system, don't play
omapoker --version
```

For your first game, choose **Play against bots**, select a buy-in with the arrow
keys, and press `Enter`. Use `C` to check or call, `F` to fold, and `N` to deal the
next hand. `Q` leaves the table and returns your stack to your bankroll;
`Ctrl+Q` quits the app, asking first if a hand is still running.

A terminal of **100 columns by 34 rows** or larger gives the table room to breathe.
The menu scrolls in smaller windows; large tables can also scroll to reach seats.

## Chips, tables and bankroll

- Every new profile starts with **100,000 chips**.
- The table ladder runs **5,000 → 100,000 in 1,000-chip steps** (96 tables).
- A table's buy-in **is** your starting stack. Blinds scale with it:
  **big blind = buy-in ÷ 100**, **small blind = big blind ÷ 2**. So the 5,000
  table plays 25/50, the 10,000 table plays 50/100, and the 100,000 table plays
  500/1,000.
- Chips move out of your bankroll when you sit down and back when you leave.
  Buy-ins and cash-outs are written to a ledger; completed hands update your
  statistics once each.
- **Your stack cannot be lost by closing the app.** Sitting down writes a note
  under `tables/` with your stack, refreshed at the end of every hand. Leaving
  puts the chips back and deletes the note; if the app is killed, the terminal
  is closed, or the machine loses power, the next start puts the stack back on
  your bankroll and says so.
- Settings has **Reset bankroll to 100,000**. This is a single-player economy, so
  nothing is being protected and the game should never dead-end.

## Modes

| Mode | Description |
|---|---|
| **Play vs Bots** | 1–8 bots, difficulty from Easy to Pro |
| **Hot-Seat** | 2–6 humans on one keyboard, with a hand-hiding reveal screen |
| **Host LAN Game** | Code-gated table; bots can fill empty seats |
| **Join Game** | Enter a code or paste a join string |

### Codes

- **Room code** — `OMA-7K3QF-92`. Generated fresh on every host start, never
  reused, and rejected if wrong. Solo and hot-seat games never need one.
- **Identity code** — `OMA-ID-4H7X-2NQ9`. Identifies a profile's signing key.
  Your bankroll, stats and assist settings stay with the local profile; typing
  an identity code on another machine does not transfer them.

Identity codes are verifiable with **Ed25519**: the profile holds the private
key, the code is derived from the public key, and a host challenges with a nonce
and verifies the signature. No shared secret ever goes over the wire. This proves *the same
key holder came back* — it is trust-on-first-use, not a certificate authority.

Host is authoritative: only the host ever knows every hole card. Hands use
**commit-reveal** — the host publishes a hash of the shuffled deck before the
hand and reveals the seed after it, so clients can verify the deal. On the host,
press `V` after a hand for the review, which includes the revealed seed.

### Playing over a LAN

1. On the host machine: **Host LAN Game**. The lobby shows a room code and a
   paste-able invite line for every local address, e.g.
   `omapoker://192.168.1.20:8760/OMA-7K3QF-92`.
2. Everyone else: **Join Game**, then type the code (or paste the whole invite
   line — it is parsed for you).
3. The lobby lists who has knocked, marks whether their identity code verified
   against their key, and lets the host kick anyone (`K`).
4. The host presses `S`. Guests are seated in the order they arrived; bots fill
   whatever seats are left.

While a hand is running:

- The host is the dealer and presses `N` for each new hand. Guests press `N`
  to say they are ready; the host sees it in the log.
- Each player only ever receives their own hole cards. The board, the pot, the
  stacks and everyone's last action are public.
- Turns are on a 30-second clock (`action_clock` in `settings.json`). A player
  who runs out of time checks if it is free and folds otherwise.
- If a guest's connection dies mid-hand, a bot takes over their seat and the
  hand finishes normally.
- If a guest loses their stack, the host buys them back in — remote players are
  the host's guests, so the host is never asked to approve somebody else's
  chips. The host's *own* bust still prompts the host.
- The host leaving ends the table for everyone, with a goodbye the guests are
  shown rather than a silent disconnect.

An invite can pack more than one address; OmaPoker picks the one a guest can
probably reach and shows the rest in the lobby if that guess is wrong. LAN
traffic is plaintext on your local network.

## The assist dial

One setting per profile, **0 to 5**, changeable mid-hand.

| Dial | What you get |
|---|---|
| 0 | Nothing. Pure poker. |
| 1 | Your hand's ranking name |
| 2 | + pot odds, outs, equity |
| 3 | + a recommended action and why *(default)* |
| 4 | + preflop charts and position advice |
| 5 | + step-by-step walkthrough and a full post-hand review |

First run asks your experience level and pre-sets the dial; override it any time.
A persistent **leak report** tracks VPIP, aggression and showdown rate, phrased
for your level.

## Music

The chip in the top right shows what is playing and doubles as a source toggle:

```
♪ ▁▃▅▂♫▁▃  Careless Whisper — George Michael   [↻]
```

- Click `↻` to cycle sources:
  `Off → firefox (Apple Music) → spotify → … → OmaPoker Local → Off`.
- Click the track text, or press `M`, to open the Music panel. In that panel,
  `C` cycles sources, `Space` plays or pauses, and `S` toggles shuffle.
- **MPRIS backend** controls any player on your system through `playerctl`,
  including your Apple Music web player in Firefox and Spotify. OmaPoker cannot
  stream DRM audio itself; it controls the player that can.
- **Local backend** plays files from folders you choose (default `~/Music`) with
  its own `mpv` instance, mutagen tags, `.m3u` playlists, shuffle and repeat.
- Because `mpv-mpris` is installed, OmaPoker's own playback also registers as a
  system MPRIS player, so your bar chip and media keys see it too.
- `M` opens the Music panel; media keys keep working system-wide.
- Switching sources pauses the previous one. `Off` only pauses what OmaPoker
  controls — it never kills a player you manage yourself.
- Quitting OmaPoker leaves its own `mpv` playing (deliberately: a table that
  stops the music when the hand ends is worse than one that does not). Only
  OmaPoker's cava visualiser is stopped. Stop the music from the Music panel,
  the chip, or your bar.

## Keys

| Key | Action |
|---|---|
| `F` | Fold |
| `C` | Check / Call |
| `R` | Raise |
| `B` | Bet |
| `X` | All in |
| `N` | Next hand (host) / ready (guest) |
| `A` | Toggle assist detail |
| `V` | Full hand review |
| `M` | Music panel |
| `?` | Glossary |
| `F1` | Help |
| `L` | Show or hide the hand log |
| `Space` `,` `.` | Play/pause, volume down, volume up |
| `Q` | Leave the table |
| `Ctrl+Q` | Quit OmaPoker |

In a LAN lobby, `S` starts the table and `K` kicks the last player who knocked.

## Uninstall

```bash
./scripts/uninstall.sh          # launcher, icon, desktop entry, venv
./scripts/uninstall.sh --purge  # ...and your profiles and bankroll (asks first)
```

Your profiles and bankrolls under `~/.config/omarchy/omapoker/` are **kept** by
default, because a bankroll is the one thing you cannot get back.

## Layout

```
~/.local/bin/omapoker                                 launcher
~/.local/share/omapoker/venv/                         managed virtualenv
~/.local/share/applications/omapoker.desktop          desktop entry
~/.config/omarchy/omapoker/profiles/                  profile + identity keys
~/.config/omarchy/omapoker/bankroll/                  chip balances + ledger
~/.config/omarchy/omapoker/stats/                     coach statistics
~/.config/omarchy/omapoker/tables/                    a sitting's stack, until it is cashed in
~/.config/omarchy/omapoker/backups/                   timestamped snapshots
~/.config/omarchy/omapoker/content/                   your own coach content (optional)
~/.config/omarchy/omapoker/settings.json              global settings
~/.local/state/omapoker/logs/                         logs, incl. gate refusals
```

Every write is atomic and backed up with a microsecond timestamp, so
lexicographic order is chronological order. If a file is ever unreadable,
OmaPoker falls back to the most recent backup rather than starting you over.

## Development

```bash
~/.local/share/omapoker/venv/bin/python -m pytest            # fast suite
~/.local/share/omapoker/venv/bin/python -m pytest -m slow    # exhaustive
```

Run these commands from the project directory. The standard suite includes a
test with separate host and guest processes playing three hands over loopback;
it needs permission to open local sockets. Both peers use temporary profiles and
bounded timeouts, and verify card privacy, deck commitments, cash-out and shutdown.

The slow suite walks all 2,598,960 five-card hands and checks the category
distribution against the known frequency table, which proves the evaluator
classifies every possible five-card hand correctly.

Test-only environment variables, so a test run never touches your machine:

| Variable | Effect |
|---|---|
| `OMAPOKER_CONFIG_DIR` | where profiles and bankrolls live |
| `OMAPOKER_STATE_DIR` | where logs live |
| `OMAPOKER_SHARE_DIR` | where the managed venv lives |
| `OMAPOKER_CONTENT_DIR` | coach content directory |
| `OMAPOKER_NO_NOTIFY` | never send a desktop notification |

`omapoker --pretend-not-omarchy` shows the refusal screen on purpose, for
checking that screen without a second machine.

## Known limits

- The Omarchy gate is breakable by a determined owner. That is inherent.
- LAN traffic is plaintext on your network. Self-signed TLS is a future option.
- Identity codes prove key continuity, not legal identity.
- LAN guests see their own cards and hand names. Detailed coaching is available
  in solo, hot-seat and host play.
- A terminal UI buys its polish from restraint, not animation.
- Music failures degrade to a working poker table; they never crash it.

## Licence

MIT. See `LICENSE`.
