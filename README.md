<div align="center">
  <img src="./public/ukp.png" width="160" alt="UsoShelf Logo">

# UsoShelf

一个本地优先的漫画书架与阅读器，让收藏、查找和阅读待在同一个地方。

[应用下载](https://apps.usotsuki-kaze.com/) · [GitHub Releases](https://github.com/UsotsukiKaze/UsoShelf/releases) · [快速开始](#快速开始) · [个人主页](https://www.usotsuki-kaze.com)

![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D4)
![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=astral&logoColor=white)

</div>

## 简介

UsoShelf 把本地图片目录整理成可搜索的书架，提供系列、收藏夹和阅读进度管理，也通过可选的 JM 信息源支持在线搜索、章节预览和收藏导入。

界面提供暖纸色亮色主题与暗色主题。Windows 桌面端使用 WebView2 窗口，Python 后端只监听本机，不需要部署服务器。

**兼容说明：**仓库名为 UsoShelf，当前应用、安装包和数据目录仍使用 **JmShelf**，沿用现有用户的数据与更新链路。本仓库维护 Windows 端；Android 端独立维护。

## 核心能力

### 书架与整理

- 导入本地图片目录，自动生成封面并按自然顺序阅读。
- 按标题、作者和标签查找，维护昵称、备注与多级收藏夹。
- 管理本地系列和 JM 来源章节，按更新、阅读频次、未读状态等排序。
- 查询时识别已入架作品，避免重复添加。

### 阅读与缓存

- 连续滚动、单页翻页、双页书本三种阅读模式，自动保存阅读位置。
- 在线预览点击后加载，按四页一组逐步展示；本地书架支持连续跨章阅读。
- 阅读 JM 来源系列时提前准备下一章，优先保证当前阅读。
- 区分可回收的阅读缓存与长期本地保存；通过状态图标和右键菜单管理。
- 预览缓存保留最近四个阅读单元，书架缓存保留最近八个；系列章节独立计入缓存池。

### JMonline

- 关键词、作者、标签查询，分类、榜单与时间范围筛选。
- 基于书架标签、作者和阅读行为生成“猜你喜欢”，支持排序与排除标签。
- 详情侧栏、章节选择、在线预览，以及一键加入书架或本地保存。
- 元数据按需写入 SQLite，推荐结果与偏好在本地持久化，不遍历抓取全站。

### 桌面体验

- 明暗主题、窗口控制、系统托盘与文件管理器联动。
- 查询、预览、后台缓存分别调度；故障图片节点可切换重试。
- 登录状态在本地保存；更新时校验包体大小和 SHA-256，并保留书库与设置。

## 快速开始

### 使用安装包

从[应用下载站](https://apps.usotsuki-kaze.com/)获取安装版或便携版。GitHub 首个 Release 发布后，也可从本仓库的 [Releases](https://github.com/UsotsukiKaze/UsoShelf/releases) 下载。

- 系统：Windows 10 / 11，x64。
- 运行环境：Microsoft Edge WebView2 Runtime、.NET Framework 4.6.2 或更高版本。
- 安装版运行 `JmShelf-Setup-<版本>-x64.exe`；便携版解压 ZIP 后运行 `JmShelf/JmShelf.exe`。

### 从源码运行

安装 [uv](https://docs.astral.sh/uv/)，然后执行：

```powershell
git clone https://github.com/UsotsukiKaze/UsoShelf.git
cd UsoShelf
uv sync --frozen --group dev
uv run --no-sync jmshelf-desktop
```

浏览器调试模式：

```powershell
uv run --no-sync jmshelf
```

访问 <http://127.0.0.1:17318>。不要把本地服务端口暴露到公网。

## 数据与隐私

- 打包版数据默认位于 `%LOCALAPPDATA%\JmShelf\data`，开发版默认位于项目的 `data/`。
- `JMSHELF_DATA_DIR` 可指定独立数据目录；迁移前请备份数据库与相关文件。
- 阅读缓存可以自动回收，**需要长期保留的内容应选择本地保存**。
- 本地账号文件、Cookie、阅读记录、偏好、图片目录和数据库都不属于源码，不应提交到仓库。
- 未配置在线信息源或第三方服务不可用时，仍可使用本地书架。

## 开发与构建

```powershell
# 测试（隔离临时数据）
.\scripts\test.ps1

# 构建主程序、独立更新器、便携包和安装器
.\scripts\build-windows.ps1

# 检查并整理已有构建为 GitHub Release 附件，不会上传
uv run python scripts/prepare-release.py
```

安装器构建需要 Inno Setup，可通过 `JMSHELF_ISCC_PATH` 指定 `ISCC.exe`。生成的 ICO 来自仓库内的 `public/ukp.png`，无需额外品牌素材。

包体只作为 Release 附件发布，**不加入 Git 历史**。发布步骤、兼容链路及校验规则见 [发布说明](docs/RELEASING.md)。

## 项目结构

```text
UsoShelf/
├─ src/jmshelf/      # FastAPI、数据库、信息源、缓存、桌面与更新器
├─ public/          # 原生 HTML / CSS / JavaScript 与按需图标
├─ packaging/       # PyInstaller、Inno Setup 和 Windows 版本资源
├─ scripts/         # 开发启动、测试、构建与 Release 准备
├─ tests/           # 后端及前端契约测试
├─ docs/            # 架构基线、设计约定与发布流程
├─ release-notes/   # 按版本保存的更新说明
├─ pyproject.toml   # 项目元信息与依赖
└─ uv.lock          # 依赖锁定文件
```

详细设计见 [架构与功能基线](docs/ARCHITECTURE_BASELINE.md)、[设计约定](docs/DESIGN.md) 和 [图标审计](docs/ICON_AUDIT.md)。

## 致谢

- [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)：JM 信息源适配。
- [pywebview](https://github.com/r0x0r/pywebview)、[FastAPI](https://github.com/fastapi/fastapi)、[Pillow](https://github.com/python-pillow/Pillow)：桌面窗口、服务与图片处理。
- [Game-Icon-Pack](https://github.com/Nieobie/Game-Icon-Pack)：部分功能图标，按 CC0 使用，许可副本随仓库保留。

本仓库暂未声明整体开源许可证；第三方依赖与素材遵循各自许可。请只访问和保存你有权使用的内容，并遵守当地法律与来源站点规则。

## 关于作者

更多项目与联系方式，请访问 **[www.usotsuki-kaze.com](https://www.usotsuki-kaze.com)**。
