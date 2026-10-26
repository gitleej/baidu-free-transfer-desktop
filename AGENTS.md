# AGENTS.md

本文件适用于整个仓库，供在本项目中工作的自动化编码代理使用。

## 项目目标

这是一个面向 Windows 的百度网盘批量转存工具，提供 Pygame 桌面界面、命令行入口、断点续传、递归组批、可调节流策略和独立 EXE 打包。

修改时优先保证：

- 用户的 Cookie、令牌和本机配置不会进入日志、测试数据、截图、提交或构建产物。
- 已完成目录可以安全跳过，异常后能够从明确断点继续。
- 文件夹保持完整；为了填满批次不得拆分文件夹。
- 百度接口的批次限制按“展开条目”计算，即文件与目录节点都计入上限。
- GUI、CLI 与核心转存逻辑共用同一套参数校验和行为。

## 代码结构

- `baidutransfer/gui.py`：Pygame 界面、布局、事件循环和后台任务线程。
- `baidutransfer/runner.py`：GUI 与 CLI 共用的任务入口和参数校验。
- `baidutransfer/settings.py`：批量、间隔、休息、重试和超时设置。
- `baidutransfer/transfer.py`：深度优先扫描、目录计数、组批和断点续传。
- `baidutransfer/apis.py`：百度网盘接口调用、错误处理和节流重试。
- `tests/`：不得访问真实网络的离线单元测试。
- `scripts/build.ps1`：Windows 单文件 EXE 构建脚本。
- `scripts/capture_ui.py`：README 界面截图生成脚本。
- `.github/workflows/release.yml`：标签触发的 Windows Release 工作流。

## 开发约束

- 使用 Python 3.10 或更高版本进行本地开发；GitHub Actions 使用 Python 3.11。
- 不要读取、打印、复制或提交真实 `config.yaml`、Cookie、`BDUSS`、`STOKEN` 等凭据。
- `config.yaml` 必须保持在 `.gitignore` 中。新增日志时也不得间接输出请求头或配置对象。
- 测试必须使用 mock、fake 或内存数据，不得调用百度或其他真实网络接口。
- 网络异常、认证错误和服务端限流必须保留清晰日志和可恢复断点，不要把失败标记为成功。
- 修改递归组批时，应保持深度优先、流式处理；不要要求扫描完整目录树后才开始第一批转存。
- 当完整文件夹放不进当前批次时，将它整体延后到下一批，并继续使用后续目录填充下一批。
- 如果单个文件夹本身超过限制，只能按现有递归规则下探处理；不要静默切分一个本应保持完整的普通文件夹。
- GUI 控件应以容器和动态尺寸定位，避免依赖会在字体或 DPI 变化后重叠的固定坐标。
- 后台转存不得阻塞 Pygame 事件循环；跨线程 UI 更新应通过已有队列或线程安全机制。
- 保持 Windows 路径、PowerShell 脚本和 PyInstaller 打包兼容性。

## 修改与验证

提交前至少运行：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q baidutransfer app_main.py
git diff --check
```

涉及 GUI 布局时还应生成并检查截图：

```powershell
python scripts/capture_ui.py docs/images/main-window.png
```

截图脚本必须保持离线，不得读取 Cookie 或发起网盘请求。涉及打包或入口变化时运行：

```powershell
.\scripts\build.ps1 -SkipEnvironmentSetup
```

如果构建环境尚未创建，则去掉 `-SkipEnvironmentSetup` 并指定可用的 Python。

## 文档与版本

- 用户可见行为、参数、默认值或操作步骤发生变化时同步更新 `README.md`。
- 发布版本时更新 `baidutransfer/__init__.py` 中的 `VERSION`，并确保 README 的版本说明一致。
- 标签使用 `vX.Y.Z`；推送标签将触发 Windows EXE 构建和 GitHub Release。
- 发布前确认工作树干净、测试通过，并核对 Release 中同时存在 `BaiduFreeTransfer.exe` 与 `SHA256SUMS.txt`。

## Git 规则

- 保留用户已有修改，不要重置、覆盖或清理无关文件。
- 每个提交聚焦一个逻辑变更，提交信息使用简洁的英文祈使式或约定式格式。
- 除非用户明确要求，不要提交本机构建目录、虚拟环境、日志、断点状态或任何凭据。
