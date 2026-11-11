# 平台与架构

产品类型和CPU架构独立：AIPC、AI Workstation、AI Server都可以采用x86_64或ARM64。开始前询问用户机器类型，已明确的回答沿用，不从CPU品牌、机箱或BMC推断。

21条自动硬盘用例按目标盘、接口、文件系统和工具能力执行。机械盘不执行SSD TRIM；硬件不适用与缺少前置分别报告。人工213/292热插拔、323/326 RAID用例仅适用于具备相应配置的Server；当前不让这些人工项目阻塞自动计划。

配置platform.product_type、model、classification_source；expected_arch可从现场发现，也可指定以核对连接目标。能力项只保留ssd_hotplug、raid_hba，格式为 {"supported": false, "source": "已核对BOM"}，未知使用null而非假定不适用。

Linux负载要求原生64位Python3.8+和工具。识别x86_64/amd64、aarch64/arm64，ARM32明确不支持。inspect记录Python/工具ELF、页大小、DMI、设备树、OS/内核；ARM没有DMI不视为硬盘故障。所有路径依据sysfs/SN/WWN，禁止硬编码/dev/sda或/dev/nvme0。

fio需具备原用例引擎；出现ENOSYS先核对原生内核、权限、容器/仿真边界，不换引擎、降低QD或关闭direct以冒充通过。SATA/SAS经控制器透传缺SMART时使用实际厂商工具补证据，不把缺失值填0。

控制端可用Windows或Linux；目标Linux路径按POSIX解析。remote_suite默认包含全部自动硬盘用例，--only才缩小范围。此范围不包含USB端口、休眠、显示或整机外围功能。

ARM64适配代码及仿真检查不等于真实ARM硬盘验收；当前现场实测为麒麟x86_64，具体边界见validation.md。
