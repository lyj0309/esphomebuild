# ESPHome GitHub Actions Remote Build Wrapper

This repository is a generic compatibility backend for ESPHome Device Builder's
native **Remote Build** protocol. It lets an ESPHome Dashboard send a build to
this backend; the backend dispatches a GitHub Actions workflow, waits for the
firmware artifact, and returns it to the Dashboard. The Dashboard can then do
the normal local OTA/install step.

The repository contains no device configuration, and normal use does not
require a separate configuration repository.

## How it works

```text
ESPHome Dashboard --Noise Remote Build--> this container
                                      --workflow_dispatch--> GitHub Actions
                                      <--firmware artifact--
ESPHome Dashboard --normal OTA/install--> device
```

The backend forwards the native Remote Build configuration bundle to Actions,
so YAML files, packages, and local components do not need to be committed to
GitHub. `secrets.yaml` is deliberately removed before dispatch; Actions uses
the encrypted `ESPHOME_SECRETS_YAML` repository secret instead.

## GitHub setup

1. Fork or use this repository and create a token that can dispatch workflows
   and read Actions artifacts in that repository. Put it in the backend
   container as `GITHUB_TOKEN`.
2. Put the real `secrets.yaml` contents in the repository Actions secret
   `ESPHOME_SECRETS_YAML`. If it is omitted, the workflow generates throw-away
   compile-only values; that firmware is not suitable for OTA or HA API use.
3. No ESPHome configuration repository is required. An optional config
   repository is only a fallback for unusually large bundles.

The public workflow is `.github/workflows/build-one.yml`. It has no matrix of
personal devices and can be called for any valid ESPHome YAML basename.

## Docker backend

Build and run the image on the same LAN as the main Dashboard:

```sh
docker build -t esphome-github-remote-builder:2026.7.4 .
docker run -d --name esphome-github-remote-builder \
  --network host --restart unless-stopped \
  --env-file ./backend.env \
  -v esphome-github-builder-data:/var/lib/esphome-builder \
  esphome-github-remote-builder:2026.7.4 \
  --remote-build-only --remote-build-port 6056 /var/lib/esphome-builder
```

There is also a ready-to-edit `backend.env.example` and
`docker-compose.yml` in this repository. Copy the example first:

```sh
cp backend.env.example backend.env
chmod 600 backend.env
${EDITOR:-vi} backend.env
docker compose up -d --build
```

`backend.env` must be root-readable only and contain at least:

```dotenv
GITHUB_TOKEN=github_pat_...
GITHUB_REPOSITORY=OWNER/REPOSITORY
GITHUB_WORKFLOW=build-one.yml
GITHUB_WORKFLOW_REF=master
```

`GITHUB_WORKFLOW_REF` selects the branch of this wrapper repository that owns
the workflow (normally `master`).

On first start the container prints a one-time pairing key and fingerprint.
In the main Dashboard open **Settings → Send builds → Pair with a build
server**, enter the backend's host and port `6056`, verify the fingerprint,
and enter the key. The pairing identity is persisted in the volume.

The backend only needs TCP `6056` from the Dashboard. Do not expose it to the
Internet; the Remote Build protocol already authenticates the paired peer.

## Optional large-bundle fallback

Normal users do not need this. GitHub limits `workflow_dispatch` inputs; if a
bundle contains large fonts/images and exceeds that limit, place those files
in a configuration repository and set:

```dotenv
GITHUB_CONFIG_REPOSITORY=OWNER/my-esphome-config
```

For a private repository, set `CONFIG_REPO_TOKEN` in the **wrapper
repository's** Actions secrets. The token is used only by Actions checkout;
it is never included in the firmware artifact.

## Development and limitations

- ESPHome and the Device Builder versions should match the Dashboard. The
  supplied image uses ESPHome `2026.7.4` and Device Builder `1.11.5`.
- GitHub's workflow input limit bounds the compressed Remote Build bundle.
  Large binary assets may need the optional `config_repo` fallback.
- The wrapper deliberately delegates non-`compile` ESPHome commands to the
  original CLI. Receiver-side jobs are compile-only; OTA/install remains on
  the main Dashboard as required by ESPHome Remote Build.
