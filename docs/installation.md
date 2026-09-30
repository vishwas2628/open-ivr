# Installing Asterisk

The system module installs Asterisk for you; this page explains what it does
and how to do it by hand (or from source).

## Option A - distribution packages (default)

```sh
sudo ./system/install.sh                # runs every step
sudo ./system/install.sh --steps 10-asterisk.sh
```

Under the hood:

```sh
# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y asterisk asterisk-dahdi sox ffmpeg

# RHEL / Rocky / Alma (Asterisk lives in EPEL)
sudo dnf install -y epel-release && sudo dnf install -y asterisk sox
```

## Option B - build from source

Useful when you need a specific version or a patched module. The known-good
release for this project is **Asterisk 22.4.0**.

```sh
sudo ./system/install.sh                # set the env var first:
SOURCE_BUILD=1 ASTERISK_VERSION=22.4.0 sudo ./system/install.sh
```

Manual version:

```sh
cd /usr/src
curl -O https://downloads.asterisk.org/asterisk/asterisk-22.4.0.tar.gz
tar -xzf asterisk-22.4.0.tar.gz
cd asterisk-22.4.0

sudo ./contrib/scripts/install_prereq install   # build dependencies
./configure                                    # run ./configure -m for menu mode
sudo make menuselect                           # pick modules (pjsip, http, ari)
make -j"$(nproc)"
sudo make install

sudo systemctl enable --now asterisk
```

Modules this project needs:

* `res_pjsip` – SIP signalling
* `res_http_websocket` – the ARI event websocket
* `res_ari` – the ARI REST API
* `app_stasis` – the dialplan application that hands calls to ARI
* `func_curl` – used by optional HTTP media fetching

`system/steps/30-ari.sh` writes `modules.conf.d/openivr.conf` with exactly
these, so a fresh install is pre-configured.

## Verify the installation

```sh
asteriskctl version                 # prints the Asterisk release
asterisk -rx "core show version"
asterisk -rx "module show like res_ari"
asterisk -rx "http show status"      # HTTP/ARI listener
```

If `http show status` says the server is not enabled, check
`/etc/asterisk/http.conf` (the installer writes it) and reload:

```sh
asteriskctl reload
```

## Dialplan quick test (no IVR involved)

```sh
asterisk -rx "channel originate Local/6001@test application Playback demo-congrats"
```

Hearing `demo-congrats` means audio, SIP and RTP all work. Only then debug the
IVR.