# JmShelf 设计说明

本文保留产品与数据约定的简要说明。当前实现的完整架构、API、存储、跨端同步、UI 图标库基线和改进优先级见 [ARCHITECTURE_BASELINE.md](ARCHITECTURE_BASELINE.md)。

## 产品定位

JmShelf 是 Windows 上的单用户、本地优先本子管理器。核心流程是“导入目录 → 自动补全信息 → 归档 → 连续阅读”，不提供账号、局域网共享或云同步。

## 技术结构

```text
Edge 应用窗口（原生 HTML/CSS/JS）
            │  localhost JSON API
Python / FastAPI 本地服务（仅 127.0.0.1）
      ├── sqlite3 数据库
      ├── 本地目录扫描与图片流
      ├── Windows 文件夹选择 / 最小化
      └── JmcomicProvider（detect / lookup / download）
                 └── 同进程直接调用 jmcomic
```

主界面没有构建工具和前端框架。后端由 uv 锁定 Python、FastAPI、uvicorn 和 jmcomic 版本，启动命令为 `uv run --no-sync jmshelf`。

JM 查询不再经过 Node/Python 文本管道，Unicode 元数据由 FastAPI 直接序列化为 UTF-8 JSON。主程序使用 `--no-sync`，不会在查询时偷偷安装或升级依赖；首次使用前需显式运行设置脚本完成 `uv sync`。

## 数据约定

- `comics.id` 是内部不可变 UUID；昵称变化不会破坏收藏夹和阅读进度。
- `nickname` 一旦设置，就作为界面主标题；原始标题仍保留在详情中。
- `source_id` 保存 JM 车牌并保持唯一。
- JM 系列按每一话的 `photo_id` 拆成独立 `comics` 记录；`series_id`、`series_title` 和 `chapter_index` 只负责保留系列关系，不把多话图片混进同一个阅读进度。
- `chapter_index` 接受整数或小数，只用于排序和展示；章节身份始终由 `photo_id` 决定，因此相同序号的补充话不会被合并。
- 用户手动建立的书架系列使用独立的 `library_series` / `library_series_members` 表，与 JM 来源系列完全分离；成员顺序决定堆叠主封面、系列名称和选本顺序。
- 作者、标签保存为 JSON 数组；多级收藏夹使用独立关系表，一个本子可进入多个目录。
- 删除书架条目只删除数据库记录，不会删除用户的图片。
- 阅读模式保存在浏览器本地设置中，三种模式共享同一条逐本阅读进度。

## 导入规则

- 选择的目录直接含图片时，将该目录视为一个本子。
- 否则，将每个包含图片的一级子目录视为一本。
- 子目录名为纯数字时，自动识别为 JM 车牌并进入 `pending` 状态；信息源可用时由界面继续自动匹配。
- 图片按自然数字顺序读取，并支持 JPG、PNG、WebP、GIF、BMP、AVIF。

## 扩展边界

`src/jmshelf/provider.py` 是信息源边界。未来可以加入其他合法 API，而不影响图库、收藏夹和阅读器。查询并行获取专辑和当前话详情；重复车牌在短 TTL 内复用结果，封面由媒体路由按需下载。下载只能由用户显式触发；队列默认并行处理 2 个任务，任务内部话数并行由焦点调度器自动控制，每话可使用 1–32 个图片线程。下载结果必须同时具有非空远端页表和完整的本地图片，接口瞬时返回 0 页时最多更换客户端重试 3 次。目标目录优先使用设置页保存的默认路径，未设置时再由用户选择。代理会注入 jmcomic 客户端，高级登录与 Cookie 仍交给 `option.yml`。
