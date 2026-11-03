# Board workspace

2026-10-21 当前实物已由用户确认是 EES-331 Rev1.0，所提供手册对应这块开发板。
板级目标器件据此采用手册的 `xc7z020clg484-1`，不再要求拆屏拍芯片或重复确认板卡身份。
完整 PS 配置审核尚未完成，尤其 DDR 训练与旧示例板延时的依据；候选仍为 `confirmed=0`。
以下带日期的旧记录保留当时边界，不覆盖本次身份确认。没有新增 bit/XSA 或硬件验证结果。

- `vivado/`：板级构建 Tcl、约束、Block Design 导出和必要工程元数据。
- `vitis/`：XSA/HWH 使用说明、ARM 应用构建和启动脚本。

不要提交 Vivado/Vitis 自动生成的缓存目录。新的构建入口见 `vivado/readme.md`，从当前
仓库源码重新打包 IP 和创建 Block Design；旧 `D:/aic/` GUI 工程仅作历史参考，不再作为
新入口的构建依赖。实际板卡的 PS DDR/MIO 配置仍待实验室确认。

2026-10-15 已审查实验资料中的 EES-331（`xc7z020clg484-1`），提取 DDR、UART、
启动拨码和下板步骤；详见[资料审查记录](../docs/verification/ees331-reference-review-2026-10-15.md)。
当时资料与实物的对应关系未确认，该身份问题已由上方 2026-10-21 用户确认解决。
旧工程仍不是完整的现代 PS 预设；`unselected` 门禁保留，`xc7z020clg400-1` 仅作历史占位，
不得作为本实物的正式器件配置。完整 PS 配置审核后再正式构建。

2026-10-16 用户报告实物 SW8 六位全 ON 后，Vivado 成功识别 `arm_dap_0 xc7z020_1`。
对此实物撤回此前五 ON、一 OFF 的建议，保留已成功的拨码和供电状态，不再按旧表移动跳帽。
详见[实物 JTAG 记录](../docs/verification/board-jtag-bringup-2026-10-16.md)；这只确认 JTAG 和
XC7Z020 身份，不确认封装/速度等级、DDR 或应用通过，不能据此绕过正式选板和构建门禁。

Vivado 2025.1 不应直接从包含中文字符的用户路径构建。源码同步到纯 ASCII 工作镜像后运行，
构建完成再把 Tcl、约束和报告回收到本仓库；不要把镜像当成第二个权威源码目录。

2026-10-17 已完成 EES-331 **未确认候选**的离线 prepare，46 项 PS 参数读回匹配，
正式构建门禁保留，没有 bit/XSA 或硬件验证。用户早些时候暂时断开 USB，随后已重新连接，
提供 15 MHz JTAG 重连成功与 COM5 枚举的反馈；这不代表 UART/DDR/ARM 通过。
详见[候选配置验证](../docs/verification/ees331-candidate-prepare-2026-10-17.md)及
[Vitis/ARM 准备清单](vitis/readme.md)；本机 ARM 工具链仍缺项。
后续[安装诊断](../docs/verification/vitis-install-diagnosis-2026-10-17.md)已定位当前为
Vivado/HLS 组合，待核对补装嵌入式开发包；当前认证提示账号资料不完整，需用户亲自处理。
