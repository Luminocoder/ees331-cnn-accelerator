# EES331 Studio v5.6 卷积流水线优化候选版

工作起始日期：2026-11-01；封存日期：2026-11-02，目录日期保留本轮工作批次。本版保留相机/预处理、单字/双字、连拍批次、`DD DD DD`五秒分页及v5.5消影修复，替换PL卷积引擎的数据供应和流水线。ARM ELF、CPU基线、模型、量化、50MHz PL时钟和测速范围沿用v5.4。

**当前为离线验证通过、待上板验收的候选版。** 完整CNN RTL仿真从25.13452ms降至2.06184ms，约改善12.19倍，包含每层缓存预加载；这是同频PL仿真比较，不能写成ARM/PL实板加速比。v5.4真实结果仍为ARM 4.94ms、ARM+PL 25.42ms。新实板结果必须重新测量。

## 使用与验收

1. 手动关闭旧工作台，接板并正常供电。打开本目录`EES331_Studio.exe`，保留完整`_internal/`和同目录文件。
2. 选择实际串口，点击“初始化并连接”，加载本版新bit和配套ELF；只打开串口无法替换旧PL。初始化只经JTAG配置RAM，不写Flash/SD。
3. 无需相机，点击“ARM / PL 性能对比”。程序先核对20张/200路分数，再预热并执行100组正式配对测量，最后恢复缓存/计时器。保持原缓存和编译条件，不改成慢ARM来制造收益。
4. 保留`logs/initialize-*/`和`logs/benchmark/*/`的完整记录。性能通过后，确认显示“79 45   ”与“01 02 03”的空白位无残影、位序和亮度正确、5秒翻页正常，再回归相机单/双字及连拍。建议复测一轮。
5. 若分数/初始化/时序出现异常，可重新用原v5.5初始化回退；原目录和日志均未覆盖。本版的显示消影仍需实物观察。

如果使用命令行，先关闭占用串口的GUI，把COM5换成实际端口，再逐条执行：

```powershell
.\EES331_Studio.exe --initialize --port COM5
.\EES331_Studio.exe --board-test --port COM5
.\EES331_Studio.exe --benchmark --port COM5
```

GUI性能测试本身包含冻结输入的分数校验；不要在GUI保持串口连接时另开命令行抢占串口。

## 已完成的验证

- 独立标量卷积金标准逐项核对28,833个输出，含负数/有符号极值、边界补零、移位、饱和、通道与层切换、缓存重载、预加载及运行中复位。
- 完整SoC AXI路径20张/200路分数，额外全零/全255/边框/两组固定随机输入的50路分数及推理中复位恢复均通过。额外输入用于实现正确性验证，不是准确率数据集。
- Vivado 2025.1完成新bit/XSA，布局布线后WNS=3.864ns、WHS=0.034ns，内部时序约束满足。数字仿真和时序收敛不能代替实板测试。
- 上位机回归、实际EXE离线分数、图片/批次/CLI、实际Tk配合固件C的模拟分页及测速流程，见`integration/release.json`和`integration/latest_validation.json`；其中MMIO与时间的模拟结果不产生硬件加速比。

当前开发未打开串口/相机/JTAG，未加载板卡。本轮只读枚举出现COM3、COM4，未出现COM5；用户连接后需选择实际端口。Windows会给重新枚举的设备分配不同COM号。

## 文件与复现

- `优化说明与验收_20261101.md`：改动、性能证据、可以证明的价值和实测步骤。
- `hardware/pl/rtl/accelerator/conv_engine.sv`：本地缓存、9路窗口供数、带标记的连续流水；容量专门覆盖冻结网络28×28×1→8和14×14×8→16。
- `tests/conv_pipeline_tb.sv`、`hardware/pl/rtl/soc/mnist_soc_top_tb.sv`：独立输出金标准及整机AXI/分层周期/复位检查。
- `integration/board-build.json`、`integration/board-evidence/`、`integration/optimization-results.json`：实际构建、综合资源、仿真原始日志和比较数据。
- `integration/pl-implementation-hashes.json`：当前PL设计输入与实际构建镜像逐文件一致（工具自动生成的运行统计除外）；`build-manifest.json`及`delivery-manifest.json`封存EXE、资源及交付文件。
- `integration/v54-board-benchmark/`：旧v5.4真实性能原始数据，保留负向结果；不代表v5.6实测。
- `hardware/BENCHMARK_PROTOCOL.md`：原ARM/PL协议和计时定义，包内路径优先。

本机依赖：Vivado2025.1、LLVM22.1.8、Python3.13.7、NumPy2.5.3、OpenCV5.0.0、Pillow12.3.0、PyInstaller6.22.3、匹配Tcl/Tk8.6。EXE本身无需另装Python；初始化仍需兼容板卡/驱动及Vivado XSDB/hw_server。未在第二台电脑从零复现，不附依赖安装包。

源码重建顺序（ASCII可写工作目录示例D:/aic；不访问硬件）：

```powershell
python -B -X utf8 .\hardware\generate_conv_vectors.py
python -B -X utf8 .\hardware\build_board.py --work-root D:/aic
python -B -X utf8 .\hardware\install_pl.py
python -B -X utf8 .\tests\build_firmware_emulator.py
```

`install_pl.py`核对成功构建的源镜像，再安装新bit和PS init、同步部署哈希和实现源码哈希；随后必须重建EXE并运行验证。包内已装入本次新载荷。ARM未改，不必重建其ELF；若改ARM，须按原固件构建流程重建并同步元数据。Vivado自动生成的`dfx_runtime.txt`运行统计不作为设计输入比对，交付文件仍有独立哈希封存。

```powershell
python -B -X utf8 .\build_candidate.py
python -B -X utf8 .\tests\validate_delivery.py --output integration
python -B -X utf8 .\tests\verify_integrity.py --record --write-manifest
python -B -X utf8 .\tests\verify_integrity.py
```

打包器支持`--pyinstaller-deps`、`--tk-root`；按本机实际依赖位置填写。修改源码或载荷后须重新打包并验证，不能只替换外层bit。本次仅准备MD及实际源码/运行文件，不生成Word/ZIP或上传。
