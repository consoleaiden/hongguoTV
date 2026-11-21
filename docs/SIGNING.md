# 发布签名与自动更新说明

本仓库 `consoleaiden/hongguoTV` 是 [N3urda/hongguoTV](https://github.com/N3urda/hongguoTV) 的 fork。
`.github/workflows/auto-sync-release.yml` 会定时同步上游 `main`，用**本仓库专用证书**构建四个
release APK 并发布到本仓库 Releases。本文说明签名材料的存放、复用方式与更新兼容边界。

## 签名材料放在哪里

私钥**不进入 Git 历史**（本仓库是 public，一旦提交即无法彻底清除，任何人都能伪造签名包）。
CI 通过四个仓库级 Secret 读取，见 Settings → Secrets and variables → Actions：

| Secret | 内容 |
| --- | --- |
| `HONGGUOTV_KEYSTORE_B64` | release keystore（PKCS12）的 base64 文本 |
| `HONGGUOTV_STORE_PASSWORD` | keystore 口令 |
| `HONGGUOTV_KEY_ALIAS` | 密钥别名，固定为 `hongguotv` |
| `HONGGUOTV_KEY_PASSWORD` | 密钥口令（与 store 口令相同） |

CI 每次运行把 base64 解码为 `release.p12`，用 JDK 的 `keytool` 转成 `release.jks`，
再通过 `HONGGUOTV_KEYSTORE` / `HONGGUOTV_STORE_PASSWORD` / `HONGGUOTV_KEY_ALIAS` /
`HONGGUOTV_KEY_PASSWORD` 四个环境变量交给上游 `kotlin-tv/app/build.gradle.kts` 的
`signingConfigs.delivery`。临时密钥文件只存在于 runner 的临时目录，job 结束时删除。

> **Secret 写入后无法再读回**。请务必把 `.p12` 文件和口令另存到离线安全位置（密码管理器 /
> 加密 U 盘）。一旦丢失且本地也没有备份，就只能重新生成证书 —— 之后所有新包都无法覆盖安装
> 旧包，用户必须卸载重装，本机收藏、观看记录会丢失。

## 当前证书

| 项 | 值 |
| --- | --- |
| 类型 | RSA 2048，自签 X.509，PKCS12 keystore |
| 别名 | `hongguotv` |
| Subject | `C=CN, O=consoleaiden, OU=hongguoTV, CN=hongguoTV Release` |
| 有效期 | 2026-11-21 → 2054-04-08 |
| 证书 SHA-256 | `c362da1c71a4c6868e34ea3b3d3327b3db9b9453955884e08aa2708d190e9d4c` |

工作流把该指纹写在 `RELEASE_CERT_SHA256` 中，每次构建后用 `apksigner verify --print-certs`
对四个 APK 逐一断言。签名不一致即失败，不会发布。这是"更新后本地数据不丢失"的硬校验：
Android 只有在签名证书一致、包名一致、versionCode 不降低时才允许覆盖安装并保留数据。

上游官方包的证书是 `3579f7a8a91bbbc44688db60fb16a5561921784af23fa92f39b849086d76ca98`，
与本仓库不同。**首次从上游官方版换到本仓库的包，必须先卸载旧版**（Android 会拒绝不同签名
覆盖），本机数据无法保留；从本仓库发布的第一版开始，后续自动更新才是无缝覆盖安装。

## 版本号与发布节奏

- 每 6 小时检查一次上游 `main`；有新提交即合并到本仓库 `main` 并触发构建发布。
- `scripts/auto_release.py version` 从上游 `build.gradle.kts` 读取 `versionName` / `versionCode`，
  结合已有 `kotlin-v*-auto.*` tag 计算严格递增的 `versionCode`。
- 产物版本形如 `0.16.11-auto.33`，tag 形如 `kotlin-v0.16.11-auto.33`。
  tag / 文件名 / versionName 都通过 `AppUpdate.parse()` 的同款正则校验，客户端才会接受。
- 每个 Release 包含四个 APK、`update.json`、`SHA256SUMS`；发布完成后置为 latest，
  使客户端能从 `/releases/latest/download/update.json` 读到更新信息。
- 同名 tag 已存在时跳过发布（幂等）；合并冲突时中止，不发布半成品。

## 应用内「检查更新」渠道

上游 `kotlin-tv/core/.../AppUpdates.kt` 把更新源硬编码为 `N3urda/hongguoTV`。
构建时 `scripts/auto_release.py patch` 会在 **CI 工作树内**把 `REPOSITORY` 常量和对应单元测试
的期望 URL 改成 `consoleaiden/hongguoTV`，让应用内更新去拉本仓库的 `update.json` 和同签名 APK。

这个改写**不提交进 Git**：`main` 的源码保持与上游一致，可避免上游改动同一文件时每次同步都
产生合并冲突。代价是从 `main` 手工本地构建出的包，更新渠道仍指向上游；只有 CI 发布的包
才指向本仓库。若上游重构了这两个文件的写法导致锚点消失，patch 步骤会明确失败并中止构建，
不会静默产出渠道错误的 APK。

## 首次启用清单

1. Settings → Actions → General：启用 Actions，Workflow permissions 选 **Read and write permissions**。
2. Settings → Secrets and variables → Actions：添加上表四个 Secret（值见交付的
   `SECRETS-TO-ADD.md`，添加后请删除该本地文件）。
3. Actions → `Auto sync upstream and release signed APK` → **Run workflow**（首次上游无新提交
   也会自动补发一版，用于产出第一个可覆盖安装的包）。
4. 确认 Release 为 latest 且含 `update.json`；装上电视后，后续自动更新即可覆盖安装保留数据。
