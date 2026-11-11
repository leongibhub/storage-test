# 配置与执行约定

配置从 assets/config.example.json 复制，先inspect再填值。示例不会自动选盘或授予写权限。不要将凭据写入JSON。

必填platform.product_type（aipc/ai_workstation/ai_server）、model、classification_source。CPU架构由inspect自动识别；可填expected_arch核对。机型能力、ARM64工具检查和完整示例见[平台说明](platforms.md)。旧配置缺平台分类将拒绝执行；能力未知不会被当成不适用。

|字段|用途、单位与边界|
|---|---|
|expected_devices|所有底层disk的serial或wwn、model、size字节；由实际枚举与实物/BOM核准。不得只填/dev/sdX。多盘映射必须完整|
|test_directory|已存在、无符号链接的绝对目录；文件系统测试使用。先核对它实际挂载于目标|
|report_root|独立绝对输出目录，不能在测试目录内；特殊电源/破坏性测试必须另机持久保存|
|authorization|沿用用户已有授权填file_write/raw_read；install_dependencies仅供Agent修复流程，不自动执行任意安装脚本|
|allow_system_disk_file_test|默认false；明确许可才可在系统底层盘的专用目录测文件，绝不授权裸写|
|max_space_bytes|专用文件峰值字节上限；不自动缩小原用例|
|min_free_bytes|始终保留的可用空间字节数；配额/并发占用还需现场检查|
|max_total_write_bytes|含准备、复制及负载的保守主机写入预算，不是NAND写放大或物理擦写量|
|max_case_seconds|整项时限秒，包含准备及所有profile；应为原用例有效负载另留准备/最后校验时间，不能以超时缩短原定负载。用户态超时不能保证终止内核不可中断I/O，必须观察真实进程状态|
|other_workload|已确认空闲或实际业务负载描述；不能自行停止其他业务|
|rate_limit_bytes_per_job|写性能负载明确批准的每job上限，计入基准条件；不设置时写性能项Blocked，不能偷偷限速后称峰值达标|
|stop_temperature_c|可选，摄氏度；需来源于目标规格/已确认要求，不能发明通用SSD温度线|
|device_spec|51的规格来源source、expected(model,size字节,tran)、nominal_capacity_bytes；标称TB=10^12，GiB=2^30；文件系统容量/剩余空间单独展示|
|baselines|键为AISTD-57:0等用例ID:零基profile编号。含source、与实测完全匹配的conditions，以及min_bw_bytes/min_iops/max_mean_latency_ns/max_p99_latency_ns。仅performance_mode=compare_baseline时用于判定；默认record_only无需基准|
|approve_verify_cycles|65需明确接受完整写/回读循环方式；4小时结束条件不取消最后一轮校验|
|cases|本机执行器的显式子集。remote_suite默认全部21条自动项，只有--only缩小范围，避免旧配置静默漏测|
|exclusions|可选，ID对应reason/source；仅用于用户明确排除或有证据的不适用，报告Skipped，不是Blocked修复手段|
|mode/raw|raw_read限定原始路径、offset_bytes、length_bytes，只读打开并按SN核对；新增项不能冒充原用例|
|validation_only|开发验证标签；validation_scale仅此模式允许seconds/size_bytes缩小，输出目录必须含validation|

## 集中收集清单

自动收集OS/架构、工具、设备信息和拓扑后，只询问剩余项：
1. 测哪块盘/namespace/分区、哪个目录：用于防错盘与确定覆盖区域。
2. 保留哪些数据、业务是否在用、可执行哪些动作：决定文件/只读/外部操作边界。
3. 选哪些用例、允许时长和写入/空间上限、最低空闲及停止条件：保护资源并保持原用例有效性。
4. BOM/性能基线及可比条件、来源：决定能否判达标；可先采性能。
5. 正常整机重启窗口、精确TRIM挂载点、隔离盘满模式：决定自动用例权限与方法。人工清单不要求现场准备。

不要求用户重复提供可由当前环境、Jira或Confluence查明的信息。外部条件尚缺时先完成脚本和可独立的只读工作。

## 阻塞处理与重跑

先看command-N.json的argv、rc、stdout/stderr，保留原始输出。区分命令缺失/权限/解析失败与已确认硬件错误。每项修复有新依据才重试，通常单原因最多2次；仍无变化则集中说明外部前置。

|原因|可执行处理|验证及停止边界|
|---|---|---|
|工具缺失/同名fio|查PATH及fio --version；读取OS包源。已有安装授权才用发行版包管理器安装fio、smartmontools、nvme-cli、util-linux|重新检查版本与架构，禁止把Python Fiona当fio|
|参数/引擎不支持|读取当前fio --enghelp及--cmdhelp；核对原用例，修正明确版本兼容问题|不得静默换ioengine/direct/QD；等价性及基准变化需明确，保留原始失败|
|健康/内核权限|检查运行用户、sudo已有授权、journal可见性；使用明确获准方式重新执行|不关闭监控，不把unknown变0；RAID盘需厂商透传工具|
|目录/拓扑变化|重新只读枚举，按稳定标识查实际挂载；在明确原目标内修正路径|盘身份不明停止写入，不自动改挂载或转另一块盘|
|空间/配额|检查df、配额和任务自身遗留文件，按inode清理本任务数据|不能删用户文件或缩小原测试后判Pass|
|缺基准|检索Jira关联需求和Confluence、批准规格，核对条件|实测数据保留，不能以工具rc0宣称达标|
|真实I/O/校验/温度故障|立即停止受影响负载、保留原始证据，查内核/健康增量|保持Fail；原因尚待验证不能抹成普通环境阻塞|
|外部操作/未适配|读原用例全部步骤，确定设备专有控制和观察接口，补可测试适配器|无完整适配或操作授权保持Blocked；不靠手填pass JSON完成|

修复后`run --rerun-from ... --repair-note ...`仅选择原Fail/Blocked，新目录保留新尝试，旧目录不覆盖。重跑不会自动沿用不匹配的新盘/新基准；每次均重新核对。历史结果和修复说明可通过previous链接追溯。

基准的顶层阈值应用于该profile每个实际读写方向，不支持方向子字典。不同读写阈值使用独立profile；混合负载若需独立方向门限或组合指标，应先扩展并验证判定逻辑。禁止用空子对象使阈值失效。

## 来源适用性

- [佰维nvme_4T硬盘测试记录](https://wiki.pcg.riverbegin.cn/pages/viewpage.action?pageId=1442038)，已读正文v2及子目录（空）。适用Biwin 4TB / Hygon3490的特定历史试验；4M、16jobs、QD256与短时缓存写测试不能套用为所有盘验收门限，也不能执行其固定裸盘写路径。
- [测试工具与规范](https://wiki.pcg.riverbegin.cn/pages/viewpage.action?pageId=1442043)，已读正文及子页目录，仅列Sleepmode/PCIe指导，未提供统一存储阈值。
- [fio官方说明](https://fio.readthedocs.io/en/latest/fio_doc.html)用于参数与输出结构核对；部署时仍查本机版本帮助。这里不复制文档示例作为本项目验收标准。


### ARM环境的libaio ENOSYS

fio列出libaio仅证明编译时包含该引擎，不证明当前内核接口可用。出现`io_queue_init error=38 / Function not implemented`应检查是否使用QEMU用户态跨架构容器。使用真实ARM64 Linux或有独立ARM64内核的完整系统虚拟机验证，不改为同步引擎、不降低QD、不关闭direct；完整虚拟机开发结果也不能作为物理盘性能基准。保留原日志，在新目录重跑；仍失败则核对内核AIO支持及限制。


performance_mode默认record_only：记录吞吐、IOPS、平均/P99延迟、字节量、时长及实际条件，采集完成记Pass并注明未做性能达标判定。仅用户要求基准对比时使用compare_baseline；不得为避免真实错误而切换模式。

## 完整计划新增配置

|字段|实际用途|
|---|---|
|ssh_host_key_sha256|已核对远端主机公钥原始字节的SHA256十六进制摘要；控制端重连必须一致|
|authorization.reboot|56正常整机重启的明确授权；控制端保存基线后执行，恢复不重复发重启|
|authorization.trim / trim.mounts|明确允许fstrim的挂载点白名单。70使用测试目录所在挂载点；212需要/及测试目录挂载点。不接受其他盘或不明确的路径|
|trim.timeout_seconds|默认600秒工具等待上限，仍受本项总时长约束；首次全盘TRIM可能比后续慢，不把超时当成功或性能失败|
|disk_full.mode|当前仅isolated_image：在目标物理盘上新建隔离ext4文件系统，真实ENOSPC和恢复，报告方法差异|
|disk_full.image_bytes|至少256MiB，默认1GiB，空间和写入都计预算；不填满宿主分区|
|storage_bom|209完整物理盘清单，含SN、型号、精确容量及接口；默认沿用expected_devices，缺全盘清单需先确认|
|expected_controller_drivers|209批准的存储控制器驱动清单；缺独立规格只记录实际绑定，不能宣称规格比对通过|

212的定时器状态直接检测，不静默启用系统服务。原步骤写放大降低无NAND计数时只记录不可评估原因；NVMe主机写入量不是NAND写入量。重启基线的1GiB由控制器从总体写预算中提前预留。
