# AISTD Storage Test

面向 HDD、SATA/SAS SSD 和 NVMe SSD 的硬盘测试 Skill。覆盖设备识别、健康检查、性能采集、数据完整性、长稳、重启保持、盘满恢复与 TRIM，并生成可追溯到 Jira 用例及原始步骤的报告。

开始测试时先确认机器属于 **AIPC、AI Workstation 或 AI Server**，再识别实际架构、磁盘与工具。会话中已经确认的信息直接沿用。CPU、内存、USB 接口、整机装配和图形工具专项不在本 Skill 范围内。

## 支持范围

|项目|说明|
|---|---|
|目标系统|Linux，Python 3.8+；完整远程控制流程需要 systemd、SSH 和相应 sudo 权限|
|架构|x86_64、ARM64；必须使用目标平台原生工具，不以跨架构仿真结果作为硬件验收|
|存储|HDD、SATA/SAS SSD、NVMe SSD；按实际设备能力判断适用性|
|控制端|可运行 Python 与 Paramiko 的 Windows/Linux 主机；重启期间保持在线|
|性能判定|默认只记录吞吐、IOPS、延迟和条件，无性能基准也可执行；不据此宣称性能达标|
|Jira|生成用例覆盖映射，不自动修改 Jira 用例或测试结果|

工具依用例检查，包括 fio、smartctl、nvme、lsblk、findmnt、lspci、fstrim、mkfs.ext4、losetup 和日志工具。RAID/HBA 可能需要厂商透传工具；不能把无法读取的健康指标当作正常。

## 安装为 Skill

将本目录完整复制到宿主的 Skills 目录，例如 Codex 的 `~/.codex/skills/storage-test/`。保留 `SKILL.md`、`scripts/`、`references/`、`assets/` 与 `agents/` 的相对位置。

重新加载或重启宿主后，确认可发现 `storage-test`。文件复制成功本身不证明宿主已加载。

可向 Agent 提出：

> 使用 storage-test 测试这台 AIPC。先识别系统和磁盘，列出完整计划，并集中确认尚缺的写入预算、重启窗口和 TRIM 授权。性能只记录实测数据。

## 配置与执行

1. 在目标 Linux 上运行只读检查，核对机器、磁盘身份及工具；也可由 Agent 通过现有连接代为检查：

   ```bash
   python3 scripts/storage_test.py inspect --out environment.json
   ```

2. 复制 `assets/config.example.json` 为自己的 `storage.json`。按[配置说明](references/configuration.md)填写真实值，再生成准备表：

   ```bash
   python scripts/storage_test.py prepare --config storage.json --out preparation
   ```

   示例中的空值和 `false` 是未确认状态，不是可直接执行的配置。需确认产品分类、完整磁盘清单、专用目录、峰值空间、最低空闲、累计写入量、时限及每 job 写入限速。系统盘文件测试、正常重启、TRIM 挂载点和隔离盘满方法分别记录授权。`ssh_host_key_sha256` 为经可信渠道核对的主机公钥原始字节 SHA256 十六进制摘要。

3. 在外部控制端安装 Paramiko，然后执行完整自动计划：

   ```bash
   python -m pip install paramiko
   python scripts/remote_suite.py --host HOST --user USER --config storage.json --out results/run-001
   ```

   交互输入 SSH/sudo 密码，不把密码放入 JSON、命令行或仓库。默认包含全部 21 条自动 Jira 用例，包括正常重启后的恢复验证；旧配置的 `cases` 不会静默缩小远程完整计划。

4. 中断后使用相同输出目录恢复；已完成收据不会再次执行：

   ```bash
   python scripts/remote_suite.py --host HOST --user USER --config storage.json --out results/run-001 --resume
   ```

仅在明确选择子集时使用 `--only`：

```bash
python scripts/remote_suite.py --host HOST --user USER --config storage.json --out results/trim-run --only AISTD-70 AISTD-212
```

完整计划含 30 分钟及多项 4 小时测试，另有准备、校验和重启时间。不得为缩短运行而降低正式用例时长或数据量。完整写入/回读循环和写入限速需在执行前确认。

## 用例映射

|类别|用例|
|---|---|
|识别、健康、基础读写|AISTD-51、52、55|
|真实重启后数据一致性|AISTD-56|
|顺序、随机与混合性能|AISTD-57、58、59、60、61、174|
|热衰减、长稳及校验|AISTD-62、63、64、65|
|盘满恢复、缓存及 TRIM 等|AISTD-68、69、70|
|设备清单及存储专项|AISTD-209、210、211、212|
|可选本地测试|NEW-FILE、NEW-RAW-READ|

详细内容、执行入口和覆盖差异见[用例映射](references/case-mapping.md)及[覆盖审查](references/coverage-review.md)。

以下 6 条保留在人工映射中，不纳入自动负载：66（环境箱高温）、67（实际断电）、213/292（物理热插拔）、323/326（涉及物理插回或更换成员盘的 RAID 恢复）。机型不适用与人工未执行分别报告，不要求人工配合来解锁自动计划。

## 结果与边界

- 保存 HTML 报告、机器可读结果、原始命令及输出、用例/步骤映射、工具与源码身份、控制收据和证据哈希。控制端持久保存重启前基线，重连后检查 boot ID、磁盘身份及 MD5/SHA256。
- 只清理本任务创建并核对身份的文件、loop 设备和挂载。没有裸盘写入或擦盘入口；盘满测试仅格式化本任务新建的普通镜像文件。
- AISTD-68 在目标盘上的隔离 ext4 镜像触发 ENOSPC，并验证释放后的恢复，不等同于填满真实业务分区。
- AISTD-209 缺独立批准驱动清单时，只能核对实际绑定，报告中保留覆盖限制。
- AISTD-212 不从主机写入计数推算 NAND 写放大；缺 NAND 计数时不宣称写放大降低。
- Pass、Fail、Blocked、Skipped 以及人工项分别展示。性能采集 Pass 不等于达到未知基准；日志或必要前置不足不能判通过。

截至 2026-11-11，已在麒麟 V10 SP1 / x86_64 / NVMe AIPC 上完成累计验证。历史运行与补跑证据合并覆盖 21 条自动 Jira 用例和 2 条可选本地项，不代表最新代码重新完整运行一遍。ARM64 真实硬件、HDD/SATA/SAS 实盘及 Server RAID 尚未完成对应实机验收。详见[验证记录](references/validation.md)。

## 开发验证

```bash
python -m unittest discover -s tests -v
```

当前 73 项检查：Windows 69 项通过、4 项 Linux 专用检查跳过；已验证的麒麟原生环境 73 项通过。开发检查不能替代真实硬件测试。运行前应重新核对目标系统、工具版本及当前用例定义。

## 目录

- `SKILL.md`：Agent 执行约定。
- `scripts/`：本机执行器、远程控制器、重启及报告实现。
- `references/`：用例定义、映射、平台、配置及验证边界。
- `assets/config.example.json`：无凭据的配置模板。
- `tests/`：执行器检查和 Linux 集成验证脚本。

随包 Jira/知识库链接需要相应访问权限；本仓库不提供现场凭据、原始测试证据或 Jira 写入权限。
