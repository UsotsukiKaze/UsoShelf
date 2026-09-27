# 历史稳定版本归档

归档日期：2026-09-27。当前推荐版本为 [1.3.4](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.3.4)。

## 归档原则

本次补发 12 个有旧站正式发行记录、安装器和便携包齐全的历史版本。保留原包，未重新构建；逐一核对大小、SHA-256、EXE 文件头、ZIP 完整性与常见私有数据路径。此验证证明原包一致性，不代表对旧版本重新完成全部功能测试。

**旧版源码快照未保留。** 历史标签指向独立的“归档说明”提交，只含本说明和校验清单，不包含应用源码。GitHub 自动生成的 Source code 压缩包无法运行或构建这些旧版，请下载 Release 附件中的安装器或便携 ZIP。当前可构建源码从 `v1.3.4` 开始，Git 导入提交的时间没有伪装成原始开发时间。

## 版本索引

以下“原站日期”来自 KazeApps 发行记录（UTC），不是此次 GitHub 补档时间。补档 Release 均设为非最新，最新版本仍保持 1.3.4。

| 版本 | 原站日期（UTC） | 主要节点 |
| --- | --- | --- |
| [1.0.0](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.0.0) | 2026-09-07 | 最早正式版：本地书架、阅读与 JM 查询 |
| [1.0.3](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.0.3) | 2026-09-08 | 应用更新替换、重启和回滚修复 |
| [1.1.1](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.1.1) | 2026-09-08 | JM 收藏同步与书库更新集中展示 |
| [1.2.0](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.2.0) | 2026-09-09 | 加入书架后应用内缓存与右键管理 |
| [1.2.1](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.2.1) | 2026-09-09 | JM 收藏加入书架 |
| [1.2.2](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.2.2) | 2026-09-10 | 单话更新订阅、删除安全与任务恢复 |
| [1.2.3](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.2.3) | 2026-09-13 | 亮暗双主题 |
| [1.2.4](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.2.4) | 2026-09-19 | JMonline 首页、榜单与搜索改版 |
| [1.2.5](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.2.5) | 2026-09-20 | 连接、分类、动画与日期修复 |
| [1.3.0](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.3.0) | 2026-09-21 | 查询调度、渐进缓存与文件操作安全 |
| [1.3.1](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.3.1) | 2026-09-23 | 预览性能、登录持久化与本地元数据 |
| [1.3.2](https://github.com/UsotsukiKaze/UsoShelf/releases/tag/v1.3.2) | 2026-09-23 | 修复后的正式版：启动、缓存边界与偏好持久化 |

各附件完整校验值、原始下载地址、精确发布时间和原始更新说明保存在 [release-history.json](release-history.json)。每个 Release 同时提供 SHA256SUMS.txt。

## 特殊处理与未补发版本

- 1.2.5：本地同名构建与旧站正式发行包不同，使用旧站取回并通过原站 SHA-256 校验的正式包；未覆盖本地同名旧构建。
- 1.3.2：使用旧站 2026-09-23 00:37 UTC 发布的修复版，非早先启动失败的包。
- 1.1.2 和 1.1.5：原记录明确用于过渡或更新流程测试，不作为重要稳定版补档。
- 1.3.3.092301：临时构建，不按正式稳定版发行。
- 1.0.1、1.1.4：当前旧站没有对应发行记录，不仅凭文件名推断为正式版。
- 1.0.2、1.1.0：同阶段已有更完整的 1.0.3、1.1.1，本次选择后续修正版作为节点。

旧版本可能保留已知缺陷；建议新安装及日常使用最新稳定版。旧站包体暂保留作为迁移回退备份，不因补档立即删除。
