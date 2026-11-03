# ARM 首次运行准备

当前尚无通过正式板卡配置门禁生成的 XSA，也未完成 ARM 应用构建。
2026-10-17 用户已重新接上 USB，提供 15 MHz JTAG 重连成功及 COM5 枚举的反馈；
尚未验证 UART 打印、DDR 或 ARM 软件，不能直接下载未确认的候选工程。

## 当前安装诊断

本机安装日志与环境脚本确认现有组合为 **Vivado ML Standard + Vitis for HLS**，
并非因为存在 `vitis.bat` 就已具备完整 ARM 开发环境。安装器资源中独立的
**Vitis Embedded Development 2025.1** 是下一步应核对的产品，先不重装 Vivado。
初始安装器报 `incomplete user information`，用户修改 profile 后仍失败；最新官网错误
明确为该账号未通过出口合规审核。请用户按错误中的[官方入口](https://account.amd.com/en/forms/export-compliance-review.html)
申请真实资料复核，或请实验室确认符合许可和适用合规要求的完整开发环境。
不能据此断定所有中国账号均被禁止下载；不通过虚报资料、借用账号或非授权网盘规避审核。
agent 未操作认证页、提交复核或安装组件。产品、位置、下载量及安装范围须在具备合法使用条件后检查摘要并确认。

独立 Arm GNU 工具链可另行核对获取条件和 Cortex-A9 兼容性，但不能仅凭编译器存在
就认为 Vitis 平台/BSP/XSA 导入/调试均可运行。当前未安装或验证该替代方案。

后续用户已进入维护安装的 Vitis Unified/Zynq-7000 摘要，但获取下载链接时返回
HTTP 400 BadRequest；新日志中的重新认证 200 不等于下载授权或合规审核已通过。
组件尚未成功补齐，不移动现有安装目录，也不把该错误当作磁盘空间不足。
为避免安装并发，暂不启动新的 Vivado；PS 门禁/来源核对使用独立 Tcl。

安装证据、账号错误处理及 JTAG 重连原始输出见
[安装诊断](../../docs/verification/vitis-install-diagnosis-2026-10-17.md)。

## 暂停账号排查后的离线工作

用户后续报告论坛重新登录也返回同样的出口合规审核错误，并要求暂停这一路排查，
转向其他工作。此后不再反复登录或下载，完整开发环境由用户向实验室/其他人确认。
这不表示账号问题已解决，也未证明 HTTP 400 的具体业务原因。

可先运行 `scripts/tests/test-arm-driver-host.ps1`，使用已有 PC GCC 和 MMIO 替身检查
未修改的驱动。25 项检查和 2 项坏输入拒绝通过，同时复现无轮询超时、记录 DONE/ERROR
优先级待确认；状态为 `TESTS_PASS_WITH_KNOWN_GAPS`。分数由 golden 注入，不能视为
ARM 编译或 CNN 板测。证据及交 A 清单见
[PC 驱动模拟检查](../../docs/verification/arm-driver-host-2026-10-17.md)。

## 离线工具检查

2026-10-18 用户报告搭档已下载 Vitis，且本机安装器已退出；搭档是否已安装、
是否包含 ARM 组件尚未核实。可将
[给搭档 agent 的分层检查清单](../../docs/handoffs/arm-development-components-checklist-2026-10-18.md)
连同下面的文件预检脚本交给对方，先验证 Cortex-A9 对象编译，有匹配 XSA 再做 BSP/应用链接。
本机真实 prepare 复验的安装并发前置已按用户反馈解除，但本轮未执行构建或改变板级验收状态。

在仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-board-toolchain.ps1 `
  -InstallRoot D:/2025.1
```

该入口只检查文件，不连接板卡、不安装组件、不修改 PATH。它在 PATH 和指定安装目录的
`gnu/`、`Vitis/` 中寻找完整的同目录 ARM 工具集，并将范围、路径、缺项和本机
Cortex-A9 CMake 配置的 SHA-256 写入 `metrics/logs/<run-id>/summary.json`。
缺项返回非零；工具找到也只代表存在，尚未证明可编译 BSP/ELF。
其他位置的工具链可用 `-ArmBin <实际bin目录>` 显式指定。

本机检查：Vivado/Vitis 启动文件存在，但上述范围未找到 `arm-none-eabi-gcc`、
`arm-none-eabi-g++`、`arm-none-eabi-ar`、`arm-none-eabi-size`。
本机 `cortexa9_toolchain.cmake` 明确引用这些工具；现有 MicroBlaze/RISC-V 编译器不能替代。
需要确认 AMD 安装器中对应的 Zynq/ARM 软件开发组件或补齐受支持的工具链后重新检查，
不要把“Vitis 能打开”当作 ARM 编译环境已完整。本轮没有安装或下载。

Vitis 2025.1 安装中有 `vitis.bat` Python CLI，帮助支持 `-s <script.py>`，
但 `bin/` 未找到旧式 `xsct.bat`。后续自动化应根据实际安装的 API 和示例编写，
不直接照搬旧 SDK/XSCT 教程。尚未验证平台、BSP、应用编译或硬件调试。

## XSA 交接条件

1. C 核对实物型号/PCB 与同版 PS 配置；`ees331-candidate.tcl` 仍为 `confirmed=0`。
2. C 使用正式审核配置运行 `scripts/build-board.ps1 -Stage bitstream`，通过时序和 DRC 门禁。
3. 将 `system.xsa`、`system.bit`、配置文件、源码哈希、构建日志和工具版本一起交 A。
4. A 使用该 XSA 创建 Cortex-A9 standalone 域与 BSP，stdout/stdin 应对应已审核的 PS UART。

工程与 Vitis workspace 使用新的纯英文路径，避免覆盖旧实验工程。
`software/arm/driver.c` 属于 A 的初稿，本轮没有修改，也不能代替 Hello World/DDR 验证。

## 重新接线后的检查顺序

- 保持已成功的 SW8 六位全 ON 和供电跳帽原位，先保存照片并重查 JTAG、实际扫描频率。
- 只读查看设备管理器的端口和 USB 设备，确认板载 UART 的 COM 口；不要选择蓝牙 COM3/COM4。
  早先查询时 USB 已断开；后续用户报告 COM5，尚待实际 UART 收发验证。
- 使用审核后的 UART 配置打开终端：115200、8N1、无流控；实际串口输出仍待验证。
- 运行 Hello World；首次成功后再做有边界的 DDR 测试。
- DDR 测试区必须从实际 linker map/内存布局中划定，避开程序、栈、堆和保留区域；
  不盲写整片 DDR，也不直接运行未审核的全内存写测试。
- 之后才验证单张十路 logits，再运行冻结 20 张与 Python golden 比较。

PS 初始化和下载 ELF/bit 均等待工具链完整及板卡参数确认。本阶段不写 Flash/SD 启动镜像。
详细证据及待发群交接见
[候选配置验证记录](../../docs/verification/ees331-candidate-prepare-2026-10-17.md)。
