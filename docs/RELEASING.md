# UsoShelf 发布与 KazeApps 同步

## 约定

- 仓库：`UsotsukiKaze/UsoShelf`；分支 `main`；发布标签 `v<版本>`。
- 应用名与包文件名继续使用 JmShelf，不改动既有用户数据路径。
- 安装包和 ZIP 作为 GitHub Release 附件，不能 `git add dist`。
- `pyproject.toml`、`src/jmshelf/__init__.py`、`packaging/JmShelf.iss`、`packaging/version-info.txt` 同步版本；`uv sync` 后提交 `uv.lock`。
- 同一版本的包体不可替换。若内容有变化，使用新版本；已有 1.3.4 应直接上传原包，不能重新构建后覆盖。

## 本地准备

```powershell
.\scripts\build-windows.ps1
uv run python scripts/prepare-release.py
git diff --check
git status --short
```

`prepare-release.py` 只检查和复制已有包体，校验 ZIP 完整性、必要文件及部分常见私有数据路径。它不能代替完整的人工发布审计。输出目录 `dist/github-v<版本>/` 包含安装器、便携 ZIP、`SHA256SUMS.txt` 和更新说明；脚本不会上传。

首次推送前必须确认忽略规则覆盖个人数据、账户文件、浏览器缓存、虚拟环境、编译工具和历史包体。只提交源码、文档、测试、必要界面资源与依赖锁文件。

## 发布到 GitHub（人工执行）

以 1.3.4 为例，先确认本地提交、GitHub 身份和所有验证均通过：

```powershell
git push -u origin main
git tag -a v1.3.4 -m "JmShelf 1.3.4"
git push origin v1.3.4
gh release create v1.3.4 --repo UsotsukiKaze/UsoShelf --verify-tag --draft --title "JmShelf 1.3.4" --notes-file dist/github-v1.3.4/RELEASE_NOTES.md dist/github-v1.3.4/JmShelf-Setup-1.3.4-x64.exe dist/github-v1.3.4/JmShelf-windows-x64-1.3.4.zip dist/github-v1.3.4/SHA256SUMS.txt
```

核对草稿附件与校验值后，再将该 Release 发布为稳定版。单纯 push 代码或 tag 不会触发包体同步；草稿与预发布也不会进入稳定更新通道。上面命令是待执行步骤，不代表已经推送。

## 新分发链路

```text
GitHub 稳定 Release（安装器 + ZIP + GitHub SHA-256）
                  ↓ 后台轮询，仅元数据
KazeApps SQLite（版本、URL、大小、校验值、同步状态）
          ├─ 网页下载按钮 → GitHub 版本固定 URL
          └─ 旧版更新清单 → 本站兼容地址 → HTTP 307 → GitHub
```

KazeApps 新增 `installer_url`、`portable_url`、来源仓库和 Release ID。网页布局不变，`/api/apps` 返回 GitHub 的 `browser_download_url`，不保存短期签名的存储节点 URL，也不代理包体。

旧 JmShelf 客户端要求清单里的 ZIP 与清单同源，因此 `latest.json` 继续使用本站地址，由本站返回 307。客户端下载实际文件仍直接连接 GitHub，并校验大小和 SHA-256，无需立即升级客户端。

默认每 600 秒检查一次并使用 ETag；首次启动后台检查。仅当两个必需附件完整、`state=uploaded`、大小有效且具有 `sha256:` 摘要时才在事务内激活新版本。仓库为空、网络错误、限流、缺附件或同版不同包时都保留当前版本。不自动降级。

KazeApps 配置：

```dotenv
KAZE_APPS_GITHUB_REPOSITORIES=jmshelf=UsotsukiKaze/UsoShelf
KAZE_APPS_GITHUB_POLL_SECONDS=600
# 可选的只读 GitHub Token；只保存在服务器环境中
KAZE_APPS_GITHUB_TOKEN=
KAZE_APPS_GITHUB_USE_ENV_PROXY=false
```

绑定该仓库的应用不再接受网页上传包体。未绑定的其他应用仍保留原发布流程。受 root 鉴权保护的 `GET/POST /api/admin/github-sync` 可查看状态或手动同步。

## 迁移与回滚

1. 先部署 KazeApps 的兼容数据库迁移与同步逻辑；原稳定版本继续可用。
2. 在 GitHub 发布与本站原版 SHA-256、大小一致的 1.3.4 附件。
3. 等待或触发同步，确认网页按钮指向 GitHub，清单兼容地址返回 307，真实下载及应用更新正常。
4. 确认后才能归档服务器旧包。当前没有 GitHub Release 时不能删除唯一可用下载源。

停止自动同步可将 `KAZE_APPS_GITHUB_REPOSITORIES` 设为空并重启服务。数据库为增量加列，旧版代码可读取；回滚代码前保留数据库备份。已经切换成外链的版本若需恢复本站下载，必须先恢复对应原包并清空该版本外链字段。

仅改成 GitHub 分发不能保证下载提速：客户端的 GitHub 连通性仍决定实际速度；本站只减少包体存储和出站带宽。

## 依据

- [GitHub Release asset API](https://docs.github.com/en/rest/releases/assets)：公开附件 URL、大小、SHA-256 摘要与状态。
- [GitHub Releases API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)：稳定版发布查询。
- [GitHub REST 最佳实践](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)：条件请求与限流处理。
