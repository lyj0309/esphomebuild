# ESPHome GitHub Actions Remote Build Wrapper

## 中文快速开始

这套 wrapper 不需要上传 ESPHome 设备配置仓库。Dashboard 会通过原生
Remote Build 协议把当前配置 bundle 交给本机容器，容器再触发 GitHub
Actions；编译产物返回 Dashboard 后，由 Dashboard 在局域网内执行 OTA。

1. Fork 本仓库，创建可触发 workflow、读取 Actions artifacts 的 GitHub
   token。
2. 在 fork 的 **Settings → Secrets and variables → Actions** 中创建
   `ESPHOME_SECRETS_YAML`，内容为本机 `secrets.yaml`。配置 bundle 中的
   `secrets.yaml` 不会发送给 workflow。
3. 复制并编辑环境文件，然后启动：

   ```sh
   cp backend.env.example backend.env
   chmod 600 backend.env
   vi backend.env
   docker compose up -d --build
   docker compose logs -f
   ```

4. 从日志记录一次性配对码和指纹。在主 ESPHome Dashboard 打开
   **Settings → Send builds → Pair with a build server**，填写运行 Docker
   的主机和端口 `6056`，核对指纹并完成配对。
5. 此后在 Dashboard 点击 Build/Install：GitHub Actions 负责编译，固件
   返回 Dashboard，OTA 仍由本机完成。

Workflow 会把 ESPHome 官方镜像的 `/cache` 作为全局共享缓存，其中包括
PlatformIO 工具链、ESP-IDF ccache，并显式启用 PlatformIO 的内容寻址编译
缓存。不同设备可安全复用相同工具和相同源码的编译结果。配置摘要仅用于
给 GitHub 的不可变缓存快照命名，不会为每台设备建立互相隔离的工具链。
GitHub Hosted Runner 每次都是临时虚拟机，因此 Workflow 还会单独缓存固定
版本的 ESPHome Docker 镜像，并在下一次运行时用 `docker load` 恢复。

下面是更完整的英文说明和可选配置。

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
- GitHub Actions persists the official container's shared `/cache` directory.
  It contains PlatformIO packages/toolchains and ESP-IDF ccache; the workflow
  also enables PlatformIO's content-addressed build cache there. Devices safely
  share matching compiler outputs. A configuration digest names immutable
  GitHub cache snapshots, but the cache contents are not partitioned by device.
- GitHub-hosted runners are ephemeral, so their Docker daemon does not survive
  between jobs. The workflow separately caches the pinned ESPHome image as a
  Docker archive and loads it before compiling instead of pulling it every run.
