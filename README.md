# ESPHome GitHub Actions Remote Build Wrapper

This repository is a generic compatibility backend for ESPHome Device Builder's
native **Remote Build** protocol. It lets an ESPHome Dashboard send a build to
this backend; the backend dispatches a GitHub Actions workflow, waits for the
firmware artifact, and returns it to the Dashboard. The Dashboard can then do
the normal local OTA/install step.

The repository contains no device configuration. Keep YAML files and custom
components in a separate configuration repository (private is recommended).

## How it works

```text
ESPHome Dashboard --Noise Remote Build--> this container
                                      --workflow_dispatch--> GitHub Actions
                                      <--firmware artifact--
ESPHome Dashboard --normal OTA/install--> device
```

The workflow accepts the current YAML as `config_b64`, so edits made in the
Dashboard do not need to be committed first. `config_repo` supplies the rest
of the configuration tree (custom components, packages, and fonts).

## GitHub setup

1. Fork or use this repository and create a token that can dispatch workflows
   and read Actions artifacts in that repository. Put it in the backend
   container as `GITHUB_TOKEN`.
2. Put the real `secrets.yaml` contents in the repository Actions secret
   `ESPHOME_SECRETS_YAML`. If it is omitted, the workflow generates throw-away
   compile-only values; that firmware is not suitable for OTA or HA API use.
3. If `config_repo` is private, add a read-only token for it as the Actions
   secret `CONFIG_REPO_TOKEN`.

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
GITHUB_CONFIG_REPOSITORY=OWNER/PRIVATE-ESPHome-CONFIG
GITHUB_CONFIG_REF=master
```

`GITHUB_WORKFLOW_REF` selects the branch of this wrapper repository that owns
the workflow (normally `master`); `GITHUB_CONFIG_REF` selects the branch of
the configuration repository.

On first start the container prints a one-time pairing key and fingerprint.
In the main Dashboard open **Settings → Send builds → Pair with a build
server**, enter the backend's host and port `6056`, verify the fingerprint,
and enter the key. The pairing identity is persisted in the volume.

The backend only needs TCP `6056` from the Dashboard. Do not expose it to the
Internet; the Remote Build protocol already authenticates the paired peer.

## Configuration repository examples

For a public config repository:

```dotenv
GITHUB_CONFIG_REPOSITORY=OWNER/my-esphome-config
```

For a private config repository, set `CONFIG_REPO_TOKEN` in the **wrapper
repository's** Actions secrets. The token is used only by Actions checkout;
it is never included in the firmware artifact.

## Development and limitations

- ESPHome and the Device Builder versions should match the Dashboard. The
  supplied image uses ESPHome `2026.7.4` and Device Builder `1.11.5`.
- The workflow input limit bounds the size of the Dashboard YAML passed as
  `config_b64`. Large trees should be kept in `config_repo`.
- The wrapper deliberately delegates non-`compile` ESPHome commands to the
  original CLI. Receiver-side jobs are compile-only; OTA/install remains on
  the main Dashboard as required by ESPHome Remote Build.
