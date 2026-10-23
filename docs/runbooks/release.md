# Signed releases

The release manifest is canonical JSON over the git commit, the compose file hash and the image ids. It is signed
with the KMS key alias/oi-key through the role oi-release-signer. The box never holds a private key.

## Build and sign

    cd /opt/oi-core
    python3 scripts/release.py build --out releases/2026-10-23-01

This writes release.json and release.sig. The signature is over the canonical manifest, so changing the commit,
the compose hash or any image id invalidates it.

## Verify

    python3 scripts/release.py verify --release releases/2026-10-23-01

A tampered manifest is refused with a non-zero exit.

## Install

    python3 scripts/release.py install --release releases/2026-10-23-01 --services api

The installer records the current image ids in rollback.json, retags the release images to the local tags the
compose file uses, recreates the services and waits for API health. If health fails, it restores the recorded
images and recreates the services.

## Roll back

    python3 scripts/release.py rollback --release releases/2026-10-23-01

## Tested on 2026-10-23

- Good release: build, sign and verify returned valid; API installed and health returned 200.
- Tampered manifest: verify was refused.
- Bad release: a signed manifest pointing at an API image with /bin/false as its entrypoint installed, failed the
  health check and rolled back automatically to the previous image. Health returned 200.
- The rollback record is in the release directory as rollback.json.

## Limits

This is a state-level release of the local images. A production release should also push image digests to a
registry, keep the release directory off the box and verify the signature with an offline public key. The KMS
key and role are real; the distribution path is the next layer.
