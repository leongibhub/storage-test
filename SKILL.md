---
name: storage-test
description: Execute HDD, SATA/SAS SSD and NVMe disk tests on Linux x86_64 or ARM64, with AIPC, AI Workstation and AI Server applicability, bounded writes, real reboot verification, TRIM, ENOSPC and Jira evidence mapping.
---

# 硬盘自动化测试

仅测试硬盘识别、健康、读写性能、数据完整性、重启保持、盘满恢复、TRIM和长稳。CPU/内存专项、USB接口、整机装配、文件管理器及图形磁盘工具均已从代码、用例定义和报告主映射移除。用户要求修改Skill不等于修改Jira；不自动写回Jira。

## 开始前

先确认目标机器及AIPC / AI Workstation / AI Server；会话已有回答直接沿用。产品分类独立于x86_64/ARM64架构。只读核对OS、原生工具、磁盘SN/WWN、精确容量、接口、固件、namespace、控制器、挂载和系统盘关系，不能猜盘或用控制端代替目标。

读取项目规则、当前Jira定义/完整步骤/评论/附件，并主动查适用公司规范。随包仅保留27条硬盘原文，见[原文](references/jira-original.json)。性能默认只记录实测，无基准不阻塞，也不宣称性能达标。

用 `storage_test.py prepare` 生成集中准备表。沿用已确认信息，只询问尚缺的目标、范围、时间/写入预算、最低空闲、重启窗口、TRIM挂载点、盘满方法和必要规格。按[配置](references/configuration.md)填入真实值；不得把示例预算当验收门槛。未知前置需提前列出，不等数小时长测结束才询问。

## 完整自动计划

默认21条：51、52、55、56、57–65、68、69、70、174、209、210、211、212。另有可选NEW-FILE、NEW-RAW-READ。精确参数和覆盖差异见[用例映射](references/case-mapping.md)。所有默认项必须进计划；不得因旧配置仅列少量cases而悄悄缩小“全部测试”。显式 `--only` 才选择子集。

完整执行使用外部控制器 `scripts/remote_suite.py`，包括跨重启的56。控制端需Paramiko；目标只需Python3.8+和原生Linux工具。密码交互输入，不放在配置、命令行、项目或报告。主机密钥SHA256需先核对。

```bash
python scripts/storage_test.py inspect --out environment.json
python scripts/storage_test.py prepare --config storage.json --out preparation
python scripts/remote_suite.py --host HOST --user USER --config storage.json --out LOCAL_REPORT
# 有完成收据则不重复执行；中断后读取controller.json并恢复同一次运行
python scripts/remote_suite.py --host HOST --user USER --config storage.json --out LOCAL_REPORT --resume
# 只执行用户明确选择的项目
python scripts/remote_suite.py --host HOST --user USER --config storage.json --out LOCAL_REPORT --only AISTD-68 AISTD-70 AISTD-209 AISTD-212
```

本机执行器 `storage_test.py run` 负责无需跨重启的项目。56不得在本机进程中假装完成；控制器保存基线、发起已授权正常重启、核对boot ID变化、同盘身份及MD5/SHA256，再清理自身文件。恢复期间不重复发出重启。常规测试失败或中断先保留现场，不继续重启。

## 执行边界和判断

- 新建文件使用O_EXCL、目录/磁盘身份检查及inode清理；保留已有用户数据。裸读仅限已批准offset/length，无裸写/擦盘入口。系统盘文件测试需单独确认。
- 63、64、65保持4小时，62保持30分钟。65按已批准完整写后校验循环；174采用已批准libaio、QD64、4job、8GiB共享文件、60秒。写入限速和其他业务状态必须进入实测条件。
- 68使用目标物理盘上的独立ext4镜像，真正触发ENOSPC、检查小文件失败及释放后的读写恢复；不填满宿主业务/系统分区。报告标注隔离范围，不冒充真实业务分区或全部业务故障验证。仅格式化本任务新建的普通镜像文件，绝不格式化用户设备。
- 70、212须有精确挂载点TRIM授权，并核对SSD、discard链路及文件系统。机械盘明确不适用；SSD不支持时查驱动/控制器，不能假Pass。212读取fstrim.timer现状，配置不符合预期记录真实失败，不自动更改系统服务。标准SMART无NAND写入量时只采集主机数据，不推导写放大降低。
- 209按完整BOM逐盘枚举、SMART扫描、NVMe查询、控制器驱动和SN对应关系保存证据；缺厂商透传工具或独立批准驱动清单须如实注明，不把当前观察值冒充批准规格。
- SMART历史计数和本轮增量分开；数据校验错误、实际I/O故障为Fail。缺依赖、权限、工具不兼容或无法证实覆盖为Blocked。自动诊断并在现有授权内修复、新目录重跑，保留旧记录。
- journald停止时不能使用旧日志判通过。仅同boot且内核环形缓冲保留自开机起，才可按单调时钟筛选本轮日志作为补充；否则Blocked。服务异常本身单独报告，不能当磁盘错误或掩盖。
- 临时loop/挂载必须记录归属和backing file，退出后检查释放。卸载失败保留镜像及证据，不强制卸载、不删除仍在用的镜像。不得按通用进程名停止他人任务。

## 人工项与交付

6条硬盘人工项：66环境箱高温、67实际断电、213/292物理热插拔、323/326包含物理成员盘插回/更换的RAID恢复。保留[人工清单](references/manual-cases.json)用于映射；不要求用户准备这些人工动作来解锁自动计划。机型不适用与人工未执行分别说明。

汇总实际Pass/Fail/Blocked/Skipped、人工项、原Jira覆盖差异、时长/限速/实测指标、工具版本及哈希、原始命令、资源清理和证据校验。开发测试不算硬件验收；ARM架构适配与ARM实机验证分开。安装与当前实测边界见[验证记录](references/validation.md)。文件存在不等于其他宿主已发现或加载Skill。
