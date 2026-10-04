# openivr Asterisk Skeleton Confs

This directory is the **source of truth** for all Asterisk configuration.
The builder (plan_for_builder.md) and installer (system/steps/*.sh) render
these templates by filling `{{TOKEN}}` placeholders and write the results
to `/etc/asterisk/`.

## Folder Structure

```
system/asterisk/
├── *.conf                    # Root configs (pjsip.conf, ari.conf, etc.)
│                             # asterisk.conf is NOT here: the installer's
│                             # /etc/asterisk/asterisk.conf is left untouched
├── extensions/
│   ├── outgoing.conf         # Outbound trunk routing (included by #tryinclude)
│   └── <ext>.conf            # Per-extension: exten => <N> in [extensions]
├── endpoints/
│   ├── .skel_endpoint.conf   # Template: PJSIP user (softphone)
│   ├── <user>.conf           # Rendered user endpoints (one per user)
│   └── trunks/
│       ├── .skel_trunk.conf  # Template: provider trunk
│       └── <trunk>.conf      # Rendered trunks (one per provider)
├── queues/
│   └── <ext>.conf            # Per-extension ringall queues (builder writes)
└── iax/
    └── <device>.conf         # IAX devices (builder writes)
```

### Include Rules

- `pjsip.conf`:
  - `#tryinclude endpoints/*.conf`          → user endpoints
  - `#tryinclude endpoints/trunks/*.conf`   → provider trunks
- `extensions.conf`:
  - `#tryinclude extensions/*.conf`         → per-extension + outgoing.conf
- Template files (`.skel_*.conf`) have a **leading dot** so `*` globs
  never match them. They are documentation, not live config.

### Section Naming Contract

All section names across *all* included files share one namespace.
Globally unique names are enforced by the builder using this pattern:

| Object Type | Section Prefix | Suffixes | Example (`user=alice`) |
|-------------|----------------|----------|------------------------|
| PJSIP user  | `<user>`       | (endpoint), `-aor`, `-auth` | `alice`, `alice-aor`, `alice-auth` |
| Trunk       | `<trunk>`      | `-auth`, `-aor`, `-out`, `-in`, `-reg`, `-identify` | `bsnl-auth`, `bsnl-out` |
| Extension   | (none)         | contributes `exten => <N>` to `[extensions]` | N/A |
| Queue       | `<ext>`        | (queue)  | `1001` in `queues/1001.conf` |

---

## Token Registry

Every `{{TOKEN}}` in the skeleton is documented here with its source.

### Installer / system.json tokens (machine-specific)

| Token | Default | Used In | Source |
|-------|---------|---------|--------|
| `{{ari_user}}` | `openivr` | ari.conf | 30-ari.sh |
| `{{ari_pass}}` | random | ari.conf | 30-ari.sh |
| `{{ari_bind}}` | `127.0.0.1` | http.conf | 30-ari.sh |
| `{{http_port}}` | `8088` | http.conf, pjsip.conf (transport-ws) | 30-ari.sh |
| `{{rtpstart}}` | `10000` | rtp.conf | 30-ari.sh |
| `{{rtpend}}` | `20000` | rtp.conf | 30-ari.sh |
| `{{stunaddr}}` | `stun.l.google.com:19302` | rtp.conf | 30-ari.sh |
| `{{external_media_address}}` | (empty) | rtp.conf | 30-ari.sh |
| `{{ice_acl}}` | `rtp-ice` | rtp.conf | (from acl.conf) |
| `{{protocol}}` | `udp` | (was in pjsip.conf, now in trunk template) | — |
| `{{port}}` | `5060` | (was in pjsip.conf, now in trunk template) | — |
| `{{host_fqdn}}` | — | (was in pjsip.conf, now in trunk template) | 40-trunk.sh |
| `{{client_uri}}` | — | (was in pjsip.conf, now in trunk template) | 40-trunk.sh |
| `{{provider_ip_1..3}}` | (empty) | (was in pjsip.conf, now in trunk template) | 40-trunk.sh |
| `{{provider_acl}}` | (empty) | (was in pjsip.conf, now in trunk template) | 40-trunk.sh |
| `{{inbound_context}}` | `from-trunk` | pjsip.conf (TRUNK-in), extensions.conf | 40-trunk.sh |
| `{{smtp_host}}` | — | voicemail.conf | 80-smtp.sh |
| `{{smtp_port}}` | `587` | voicemail.conf | 80-smtp.sh |
| `{{smtp_user}}` | — | voicemail.conf | 80-smtp.sh |
| `{{smtp_pass}}` | — | voicemail.conf | 80-smtp.sh |
| `{{smtp_from}}` | — | voicemail.conf | 80-smtp.sh |

### Builder / user-input tokens (form-driven)

| Token | Default | Used In | Meaning |
|-------|---------|---------|---------|
| `{{stasis_app}}` | `openivr` | ari.conf, extensions.conf [globals] | static ARI Stasis app name |
| `{{ring_timeout}}` | `45` | extensions.conf, queues.conf, outgoing.conf | Seconds an extension/queue rings |
| `{{dial_timeout}}` | `60` | extensions.conf [globals], outgoing.conf | Outbound trunk dial timeout |
| `{{max_call_duration}}` | `5400` | extensions.conf [globals] | Hard ceiling on any call |
| `{{company_id}}` | `openivr` | extensions.conf [globals], outgoing.conf | Default CDR accountcode |
| `{{country_code}}` | `+91` | extensions/outgoing.conf | Default country code for national numbers |
| `{{outbound_cli}}` | (required) | extensions/outgoing.conf | Outbound caller ID (E.164) |
| `{{outbound_recording}}` | `0` | extensions/outgoing.conf | Enable MixMonitor on outbound |
| `{{recording_path}}` | `/var/spool/asterisk/monitor` | extensions/outgoing.conf | MixMonitor file directory |
| `{{trunk_out_endpoint}}` | (required) | extensions/outgoing.conf | Name of trunk's outbound endpoint (`<trunk>-out`) |
| `{{trunk_transport}}` | `transport-udp` | endpoints/trunks/.skel_trunk.conf | Transport for this trunk |
| `{{trunk_username}}` | (required) | endpoints/trunks/.skel_trunk.conf | Trunk auth username |
| `{{trunk_password}}` | (required) | endpoints/trunks/.skel_trunk.conf | Trunk auth password |
| `{{trunk_ip_1..3}}` | (empty) | endpoints/trunks/.skel_trunk.conf | Provider IP ranges for identify |
| `{{trunk_acl}}` | (empty) | acl.conf | Named ACL for this trunk's IPs |
| `{{iax_provider_ip_1..2}}` | (empty) | acl.conf | IAX peer permit ranges |
| `{{queue_strategy}}` | `ringall` | queues.conf [general] | Queue strategy |
| `{{max_rings}}` | `3` | queues.conf [general] | Max ring cycles in queue |

### Per-object tokens (filled at render time)

| Template | Token | Becomes |
|----------|-------|---------|
| `.skel_endpoint.conf` | `USERNAME` | extension number (e.g. `6001`) |
| `.skel_endpoint.conf` | `SECRET_PASSWORD` | generated SIP password |
| `.skel_trunk.conf` | `TRUNK` | trunk name (e.g. `bsnl`) |

---

## Dialplan Context Registry

| Context | Defined In | Purpose |
|---------|------------|---------|
| `[from-trunk]` | extensions.conf | Inbound trunk entry → Stasis |
| `[openivr-ivr]` | extensions.conf | Static IVR entry (Local/@openivr-ivr) |
| `[openivr-dial]` | extensions.conf | `continueInDialplan` target for IVR dial actions |
| `[extensions]` | extensions.conf + extensions/*.conf | Shared context: all endpoints point here |
| `[outgoing]` | extensions/outgoing.conf | Normalize national numbers to E.164 |
| `[outgoing-common]` | extensions/outgoing.conf | CDR tag, Dial trunk, handle result |
| `[outgoing-record]` | extensions/outgoing.conf | MixMonitor gosub (A-leg only) |

All other contexts (per-extension ring logic) live in `extensions/<N>.conf`
and contribute `exten => <N>` lines to `[extensions]`.

---

## CDR Architecture

| Layer | Mechanism | Purpose |
|-------|-----------|---------|
| Call record | `cdr.conf [csv]` + `cdr_csv.so` | Authoritative call log (billing, compliance) |
| IVR event log | `openivr/cdr.py` → `data/logs/cdr.csv` | Menu navigation, DTMF, timings (debug only) |
| Correlation key | `${UNIQUEID}` (Asterisk) = `call_id` (IVR log) | Same value in both systems |

**Do not** use `openivr/cdr.py` for call records. Set `CHANNEL(accountcode)` and
`CDR(userfield)` in the dialplan (done in `[openivr-dial]` and `[outgoing-common]`)
to tag Asterisk's CDR with tenant and flow info.

---

## Generated File Conventions

- **Permissions**: 0640, owner `asterisk:asterisk`
- **Encoding**: UTF-8, LF line endings
- **Comments**: `; managed by openivr …` header on every generated file
- **Validation**: `asterisk -rx "config show like skeleton"` should show no errors

---

## Upgrade Notes

- `cdr_pgsql.conf` was **removed**. Asterisk uses `cdr_csv.so` only.
- `cel.so` stays loaded (built-in) with no backends; `stasis.conf` no longer
  declines CEL/CDR message types.
- `manager.so` is loaded (built-in) but `manager.conf [general] enabled = no`.
- `originate.py` is **LEGACY** — `dial.mode: dialplan` is the supported path.
- `system/steps/30-ari.sh` no longer writes `modules.conf.d/openivr.conf`.
- `asterisk.conf` is **not** part of the skeleton any more - `make install`
  leaves the distro's `/etc/asterisk/asterisk.conf` alone.
- `ari.conf` declares two Stasis apps: `openivr` (the IVR) and
  `openivr-admin` (monitoring). The IVR app name is static.