# EES331 INT8 CNN accelerator and digit recognition

基于EES-331 / Zynq-7020（xc7z020clg484-1）的手写数字识别系统：Windows相机与预处理 → UART → ARM Cortex-A9 → AXI-Lite → 自研INT8 CNN → 八位数码管。

**最终整理版：2026-11-03；程序与板端载荷沿用已实测v5.6。** 同板同输入，100组正式配对中ARM均值4.937098ms，ARM+PL均值2.349775ms，实测2.1011倍加速。原始数据见[新性能日志](metrics/benchmark-v56/results.json)，[旧负向结果](metrics/benchmark-v54/results.json)保留。不是相机端到端倍率，也不代表独立实拍准确率。

仓库：https://github.com/Luminocoder/ees331-cnn-accelerator

## 直接运行

免安装程序单独保存在相邻`03_可运行程序/ees331_runtime_v56/`，双击其中的`ees331_studio.exe`，必须保留同目录`_internal/`。本机不另需Python；板卡初始化仍需Vivado2025.1及USB/JTAG/串口驱动。连接唯一USB供电与传数据，打开板卡开关，选择实际COM端口，先执行“初始化并连接”。COM5仅是原机器示例。初始化仅经JTAG加载RAM，不写Flash/SD。

相机可选：不接相机也可运行“板子自检”和“ARM / PL 性能对比”。相机演示支持自动定位、实时框、单字/双字、连续拍摄与5秒分页；个位数补零，每页三个`DD DD DD`，空白位不点亮。

Git仓库不存放大型运行时DLL。若从GitHub克隆，使用Windows x64 Python3.13（带Tcl/Tk），安装依赖后运行：

```powershell
python -m pip install -r requirements.txt
python -B -X utf8 source/ees331_studio.py --offline
```

移除`--offline`才允许在界面中显式连接硬件。源码和现有串口实现仅支持Windows；未声称Linux/macOS兼容。

## 目录

| 路径 | 内容 |
|---|---|
| `source/` | 相机、预处理、GUI、JTAG初始化、串口、连拍、分页、PC金标准与测速 |
| `hardware/arm/` | 无BSP ARM固件、单核标量C CNN、公平计时与平台控制 |
| `hardware/pl/` | CNN/AXI/显示RTL、板级Tcl、约束、参考模型与训练/量化代码 |
| `resources/` | 实测bit/ELF/PS脚本、固定权重、20张输入、200路期望分数 |
| `tests/` | 源码/实际C/离线EXE/图像/分页/协议回归、RTL Testbench |
| `examples/` | 离线图片和批次清单 |
| `metrics/` | 真实新旧测速、初始化、批次与本轮整理验证 |
| `integration/` | 封存构建、实现、原始回归与源镜像证据；历史pending不覆盖实测 |
| `extras/peer_mlp/` | 队友MLP训练/对照源码；不是FPGA部署模型 |
| `provenance/` | 原构建清单、旧说明、原件复制与文件名映射 |
| `third_party/` | 随附依赖许可；不更改原作者许可 |
| 项目根`保留历史_20261103/` | 原始设备/相机日志和去重历史，不在代码提交包内，不上传GitHub |

## 构建、验证与证据

- [复现顺序](docs/reproduce.md)：环境、源运行、RTL/ARM/EXE重建、上板步骤。
- [验证与性能边界](docs/verification.md)：源数据、时基、统计范围、时序资源和未测项。
- [代码来源与分工](docs/contributors.md)：各模块来源，不将MLP冒充板端CNN。
- [许可说明](docs/licensing.md)：保留贡献者及第三方权利，未替队伍指定新的统一开源许可证。
- [AI协作记录](docs/ai_collaboration.md)和[可复用工作流](skill/readme.md)。

```powershell
python -B -X utf8 scripts/verify_release.py
python -B -X utf8 scripts/validate_release.py --output validation
```

第一条核对交付文件/源程序/部署哈希，第二条运行源码离线回归，不打开相机/串口/JTAG。需要同时验收配套EXE时，加`--runtime <ees331_runtime_v56目录>`；Git克隆不含EXE，脚本会明确标记跳过二进制验收。完整EXE验收的已保存结果见`metrics/packaging_validation.json`。

文件名按英文小写整理，产品源码、ARM/RTL及权重内容保留；两处测试入口只做路径参数适配，原件和差异哈希见`provenance/packaging_adaptations.json`。原源码中少量大小写路径沿用封存版本，依赖Windows大小写不敏感行为。二进制与数据未改，完整映射见`provenance/copy_manifest.json`。EXE标题中“候选/待上板”为封存时文字；当前实测状态以`release_status.json`及2026-11-02原始板端日志为准。

报告、现场照片、视频由用户单独维护，不在本次代码同步中改写。尚未做第二台干净电脑完整复现、第二轮跨上电测速、独立实拍准确率或功耗测量。
