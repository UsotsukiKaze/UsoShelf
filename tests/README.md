# 测试目录

- `test_*.py`：日常回归测试，运行 `./scripts/test.ps1`。
- `manual/`：需要额外工具或人工触发的界面测试，不参与默认 pytest。

测试数据库、更新器沙盒和缓存统一写入系统临时目录，并在测试结束后自动删除。
需要保留失败现场时可运行 `./scripts/test.ps1 -KeepArtifacts`。
