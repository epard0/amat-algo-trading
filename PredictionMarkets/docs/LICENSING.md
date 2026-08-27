# Licensing, activation, and sharing detection

How a licence key is made, what the database keeps, and what the anti-sharing
check does. For whoever maintains this — including the version of you that
comes back in six months wondering why it is shaped this way.

---

## What this can and cannot do

The product ships as readable source. Anyone determined to run it without
paying can delete the licence check in under a minute, and no amount of
obfuscation would change that; it would only make the code worse for the people
who did pay.

So this is not DRM and is not built as if it were. What it catches is the
realistic failure: **one key quietly circulating**. A key sold once and running
on fourteen machines in five time zones looks nothing like one person's laptop
and desktop, and it is easy to see in aggregate.

The output is therefore evidence for a human, not enforcement by a machine. The
high band exists to stop the twentieth activation of a key sold once. The middle
band exists to put a licence in front of someone who can decide.

---

## The key

```
KX15-XXXXX-XXXXX-XXXXX-XXXXX
```

Nineteen characters drawn one at a time by `secrets.choice` from a 32-symbol
alphabet — **95 bits** — plus one check character.

`secrets` rather than `random`: the Mersenne twister's entire internal state is
recoverable from 624 outputs, so a few hundred published keys would let anyone
generate the rest. `secrets.choice` also rejection-samples, so there is no
modulo bias; `tests/test_license_keys.py` asserts the alphabet comes out
uniform, because 32 dividing 256 makes that true by accident here and accidents
are worth pinning down.

The alphabet omits **I, L, O and U**, so a key can be read off a receipt or
down a phone. `normalise_license_key` maps those four back to what a human
meant (`O`→`0`, `I`/`L`→`1`, `U`→`V`), which is safe precisely because they
never appear in a generated key.

The **check character** is a SHA-256 digest of the random part folded into the
alphabet. It authenticates nothing — the derivation is public and is not
claimed to be otherwise. It rejects 31 out of every 32 single-character typos
before any database or network work happens, which is the whole reason it costs
a character.

### At rest

The database stores `key_hash`, an HMAC-SHA-256 of the canonical key under a
server secret. It does not store the key.

A copy of `storefront.db` — a backup on a laptop, a snapshot in object storage,
an errant `scp` — therefore yields no working licence unless the attacker also
holds the secret, which lives in the environment. Keyed rather than plain, so a
stolen database cannot be attacked with a precomputed table.

One exception, deliberately: for `REVEAL_WINDOW_HOURS` (24) after purchase the
plaintext is kept alongside the Stripe session id, because the success page has
to survive a refresh. Rows are purged on the next read rather than by a timer,
so an idle deployment does not sit on plaintext waiting for a sweep that never
runs. After that window the key exists only in the customer's inbox — which is
why `/api/resend` re-sends a *download link* and cannot re-send the key.

Below the HTTP layer, **everything keys on the hash**: tokens, deliveries,
revocation, activation. `store.hash_of(key)` is the only bridge.

### The secret

`LICENSE_SECRET`, falling back to `DOWNLOAD_SIGNING_SECRET`, falling back to a
`0600` file beside the database. The file is weaker — a leak that takes the
directory takes both — but far stronger than plaintext keys, and it means a
fresh checkout and the test suite work without ceremony. The startup log says
which applies. **Set the environment variable in production.**

Rotating the secret invalidates every licence ever sold. There is no migration
for that and there should not be one; if it ever has to happen, the answer is
to re-issue keys.

---

## Activation

`licensing/` on the client, `storefront/fraud.py` on the server.

The client is run by the installer once and by the `kx15` launcher before each
start. The launcher path is cheap: it reads a cached grant and only re-asks
once a week.

**Nothing in `licensing/` is imported by `bot.py`, and nothing may ever be.** A
network call on the trading path is a new way to mishandle a position, and a
licence server having a bad day must not stop a settled trade being recorded.
`tests/test_activation_client.py` asserts this.

### What is sent

| Field | Why it is there |
| --- | --- |
| `machine_hash` | The only signal that separates "my laptop and my desktop" from "eleven strangers" |
| `os_family`, `arch` | One person rarely runs Linux, macOS and Windows at once. Also the only support-useful fields |
| `app_version` | An old version beside a new one on one key suggests copies, not upgrades |
| `tz_offset_minutes` | A coarse location proxy: one integer, no city, no coordinates |

The server additionally sees the request's source address, which it **never
stores** — only a keyed hash of the /24 or /48 prefix, enough to tell "same
network" from "different network" and not enough to place anyone.

`machine_hash` is computed on the client from the machine id, hostname,
username, home directory and a random install id written on first run. Those
inputs never leave the machine. The digest is **salted with the licence key**,
so the same machine under two licences produces unrelated values and cannot be
followed between them.

Deliberately not collected: file listings, process lists, installed software,
hardware serials, MAC addresses, precise location, or anything about what the
bot trades. None would improve detection enough over the four fields above to
justify holding them.

`kx15 licence --show-report` prints the exact payload and sends nothing. Point
a suspicious customer at it.

### Scoring

`RiskPolicy` holds every threshold in one dataclass. The signals:

| Signal | Fires when |
| --- | --- |
| `device_count` | More than 3 machines in 30 days, 15 points each, capped at 45 |
| `new_device_burst` | 3+ machines first seen within 24h — a link being passed around |
| `network_spread` | More than 4 distinct network prefixes in the window |
| `timezone_spread` | More than one UTC offset, 20 points each |
| `concurrent_use` | Two machines active within 10 minutes |
| `impossible_travel` | A 4h+ offset change inside 3h. A VPN does this too, which is why it scores rather than decides |
| `os_spread` | Three or more operating system families |

Bands: **under 40** allow, **40–69** allow and flag, **70+** block.

Two exemptions, both load-bearing:

- **The first activation of a licence is always allowed.** There is nothing to
  compare it against.
- **A machine that was allowed before is never blocked.** Cutting off a running
  installation is a support incident and possibly an open position, and the
  sharing it would punish has already happened. It downgrades to `review`;
  revoking is a human decision with a human on the other end.

The thresholds are a starting point chosen from what a legitimate owner
plausibly does — a laptop, a desktop, maybe a VPS — not from measured data,
because there is none yet. **Re-fit them once real activations exist.** The
band boundaries are far more likely to be wrong than the signals.

### Failing open

Every client path allows on failure: DNS, TLS, timeout, 5xx, malformed JSON, no
cached grant at all. The only thing that blocks is the server answering, in
time, with `allow: false`.

This is a deliberate trade. Failing closed would make the seller's uptime a
dependency of somebody else's money; the worst case of failing open is one
shared licence going unnoticed for a day. If you are tempted to change it,
notice that a determined sharer can simply block the domain, so failing closed
buys nothing against the adversary and costs everything against the customer.

### Operator view

`GET /api/fraud` lists licences on three or more machines, by hash prefix.
Internal port only, like `/api/deliveries`. Revoking is `store.revoke(key)`,
which also kills outstanding download links.

---

## What an audit found, and what it changed

Every one of these was live. They are recorded because the reasoning that
produced them is the reasoning that will produce the next ones.

**A seller's block was ignored exactly when it mattered.** The check for a
manually blocked device sat *after* the known-machine exemption, and that
exemption fires only in the block band — so the higher a licence scored, the
more certainly the ban was skipped. An explicit human decision now outranks
every heuristic; it is the first thing `assess` returns on.

**`kx15 update` deleted the buyer's trading history.** Relative state paths
resolved against the package root, so the ledger, decision log and mode control
file were written into the install directory — which the updater replaces
wholesale and then `rm -rf`s. `kx15 where` reported the config directory, so the
files were not even where the tool said. The launcher now exports
`KX15_STATE_DIR`, and `config.py` resolves relative state paths against it.

**Licence keys travelled in URLs.** `/api/validate?key=` and `/api/install?key=`
put a credential in the nginx access log, in every proxy in between, and in the
`Referer` of whatever came next. Both take a header now, and nothing accepts a
key from the query string.

**`/api/fraud` was public.** nginx blocked `/api/deliveries`; `/api/fraud` was
added later and inherited no rule, so every flagged customer's hash prefix,
device count and network count was readable by anyone. The application now
refuses both off-loopback without `OPERATOR_TOKEN`, and the nginx rule matches a
pattern rather than one path. A proxy config is a separate artifact that drifts;
the guard belongs where the data is.

**One secret did two jobs.** The master secret keyed both the licence hash and
the network hash. Now each purpose gets a subkey derived under its own label.
Changing a label invalidates every value derived under it — which is why the
labels carry a version.

**A short secret was accepted in silence.** Nothing fails with a weak one until
somebody with a copy of the database brute-forces it, by which point every key
is forged. Under 32 characters is now a startup error with a command to generate
a real one.

**One valid key could grow the database without bound.** Fingerprints are
attacker-chosen, so 600 activations meant 600 device rows and a 60ms quadratic
scan per request. Devices are capped at 50 per licence, events at 400, and the
pairwise scans are bounded: 600 activations now cost 50 rows and 3ms.

## Privacy and law

The activation check collects personal data — a device identifier and a coarse
network identifier are personal data under GDPR even hashed, because they
single out a person.

What is implemented: minimisation (four fields and two hashes), local hashing,
purpose limitation, a **180-day** retention cap enforced on every write, and a
full disclosure on `/privacy` covering exactly what is sent, what is not, that
it fails open, and how to inspect the payload.

What is not, and cannot be, implemented in code: whether legitimate interest is
the right lawful basis in your jurisdiction, whether the balancing test holds,
and whether the terms are enforceable where you sell. **Have a lawyer read the
terms and privacy pages before taking real money.** The engineering here is
sound; the legal question is not one this file can answer.

If you ever widen collection, the privacy page has to change in the same
commit. It said "the Software does not phone home" for a while after that
stopped being true, which is exactly the failure mode to avoid — there is now a
test asserting the page describes the check.

---

## Two concurrency bugs worth remembering

Both were intermittent 500s on the download path, and both are in
`LicenseStore`.

1. Under Python's implicit transaction handling, Flask's request threads and
   the background fulfilment thread shared a single `BEGIN`; whichever called
   `commit()` second raised *"cannot commit - no transaction is active"*. Fixed
   by `isolation_level=None` (autocommit). Every write is a single statement,
   so statement-level transactions lose nothing.
2. `check_same_thread=False` removes the *check*, not the requirement. Two
   threads calling `execute()` on one connection raise *"bad parameter or other
   API misuse"*. Fixed by routing everything through `_write`/`_one`/`_all`,
   which hold a lock and fetch rows before releasing it.

`tests/test_storefront_app.py` has an eight-thread hammer test for each. If you
add a method that touches `self._connection` directly, you have reopened the
second one.
