# Dune IMAX watch — Cinema City Praha Flora

Watches the public Cinema City schedule for **Duna: část třetí** in the **IMAX VOLVO** hall
(70mm, the only IMAX in Prague) and pushes to your phone via [ntfy](https://ntfy.sh) when:

| Push | When |
| --- | --- |
| 🚨 *N new screening(s)* | new dates appear, with the time sales open (so far: **09:00**, day after publishing) |
| 🚨 *Sales just opened* | the sale-start time of a known screening passes |
| 🎟️ *N non-front-row seats free* | free seats rise ≥ `MIN_SEATS` above that screening's **floor**: the lowest free count ever seen, i.e. the front-row leftovers nobody buys. A purchase or cancellation in the front rows alone doesn't trigger it. |

Tapping the push opens that screening's booking page. You pick the seats and pay. The bot does
neither: the seat map is behind reCAPTCHA, and payment stays with you.

## Setup (≈10 min, one time)

1. **Phone:** install **ntfy** (App Store / Google Play) → `+` → subscribe to the topic in
   `.ntfy-topic` (in this folder). In iOS settings, allow notifications + Time Sensitive.
2. **GitHub** (free account): New repository → name `dune-imax-watch` → **Public**
   (public = unlimited free Actions minutes; nothing sensitive is in the repo) → create.
3. On the repo page, *uploading an existing file* → drag in `watch.py`, `README.md`, the `state`
   folder and the `.github` folder (in Finder press ⌘⇧. to show the hidden `.github`) → Commit.
4. **Settings → Secrets and variables → Actions → New repository secret**:
   name `NTFY_TOPIC`, value = the topic from step 1.
5. **Actions** tab → enable workflows → *Dune IMAX watch* → **Run workflow** with
   *Send a test push only* ticked. Your phone should buzz within a minute.

From then on it runs every hour, polling every 90 s, with no laptop needed.

## Tuning (in `.github/workflows/watch.yml`)

- `MIN_SEATS` — alert only when at least this many seats free up (default 2).
- `TO_DATE` — ignore screenings after a date.
- *Run workflow → Push the full current list* — a snapshot of what's on sale now.

## Run locally instead

```bash
NTFY_TOPIC=$(cat .ntfy-topic) python3 watch.py --loop 90
```

## Limits

- Free-seat counts are estimates (availability ratio × 385 seats). The API doesn't say *which*
  seats are free, so the floor logic is an inference: almost always right, but a front-row
  seat bought and later refunded can cause an occasional false alarm. It also can't tell whether
  the freed seats sit next to each other.
- GitHub can delay or skip scheduled runs at busy times, so coverage is near-continuous, not
  guaranteed. Freed seats go within minutes either way, so act on the push straight away.
- When the film's run ends, disable the workflow (Actions → ⋯ → Disable).
