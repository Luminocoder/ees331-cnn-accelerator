# 已验证事实与限制

正式数据：`metrics/benchmark-v56/results.json`，BOARD_BENCHMARK_PASS，valid_for_hardware_report=true。20校验、10预热、100正式配对全部10路INT32 logits一致；正式20图各5轮，CPU先与PL先各50组。只有一轮完整上电会话。

| 路径 | 均值ms | 中位数ms | P95 ms |
|---|---:|---:|---:|
| ARM CPU0标量C | 4.937098 | 4.937159 | 4.940802 |
| ARM调用PL | 2.349775 | 2.349458 | 2.351436 |

主加速比164569931/78325846=2.101093565倍，耗时降低52.41%。边界为ARM内存784字节uint8到10路INT32回ARM数组，PL包含量化/AXI写入、启动/轮询/读回及每层缓存预加载；不含相机、预处理、UART、初始化、显示、日志及稳态前的权重准备。

ARM约666.7MHz、PL50MHz、板端timer333.3MHz。两条路径相同MMU/L1配置，DDR inner WBWA/outer NC，不用L2；CPU单核C -O2，无NEON/LTO。不扣计时器开销。P95为升序第95项。ARM仍轮询，不声称释放CPU或并发收益。

旧v5.4 ARM+PL均值25.422408ms、倍率0.194213，原始负向数据保留。新旧ARM+PL实板改善10.82倍；新旧PL busy仿真1256726→103092周期，改善12.19倍。它们与主ARM/PL倍率2.1011不同。

布局布线：7330 LUT、2252 FF、20 BRAM tile、5 DSP、24 IOB；50MHz下WNS+3.864ns、WHS+0.034ns、TNS/THS为0。内部无未约束端点/无时钟/组合环；显示输出未施加output_delay，DRC有31个warning，无error，不能宣称所有I/O约束完整或已验证Fmax。原始报告见`integration/board-evidence/`；旧资源见`metrics/baseline_utilization_post_route.rpt`。

数码管消影来自用户2026-11-02现场目视确认，分页ACK与约5秒间隔见两份batch JSON。保留历史pending字段；软件ACK不是实物观察证据。

相机开发回放、冻结20图一致性和展示成功案例都不能当作独立实拍准确率。未测整板功耗/能耗、相机到显示帧率，未完成第二台干净电脑及第二轮跨上电完整性能验证。此次整理不访问硬件。

## 本轮文件整理验证（2026-11-03）

整理目录与独立Git克隆副本均通过74项既有源码检查。完整配套EXE在独立运行目录通过200/200冻结分数、初始化资源离线校验、8个受控图片案例、空白拒绝、9个数字的实际张量回放和连拍分页。独立源码克隆不携带EXE/DLL，重新编译真实C主机harness后通过源码检查、200路金标准和初始化资源校验。证据见`metrics/packaging_validation.json`和`metrics/source_clone_validation.json`及对应日志目录。

以上是同一台电脑不同目录的离线验证，不是第二台电脑的干净复现，也不是新的实板性能测试。产品source目录、ARM/RTL、位流、固件和权重未改；两处测试脚本仅增加外部运行目录/显式历史日志参数，原件和前后哈希均保留。
