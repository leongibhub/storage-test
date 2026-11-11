# 硬盘用例与执行映射

21条Jira自动项＋2条可选本地项；6条人工项。默认完整计划使用remote_suite.py。

|用例|测试内容|执行入口|
|---|---|---|
|AISTD-51|验证磁盘型号/容量/接口识别|identity|
|AISTD-52|验证健康状态正常|health|
|AISTD-55|验证基础读写|basic|
|AISTD-56|验证重启后一致性|reboot|
|AISTD-57|验证顺序写吞吐|fio|
|AISTD-58|验证顺序读吞吐|fio|
|AISTD-59|验证随机读IOPS|fio|
|AISTD-60|验证随机写IOPS|fio|
|AISTD-61|验证业务类混合负载性能|fio|
|AISTD-62|验证长写后的热衰减|fio|
|AISTD-63|验证长时间顺序写稳定性|fio|
|AISTD-64|验证长稳|fio|
|AISTD-65|验证长时读写数据一致性|fio|
|AISTD-68|验证盘满时业务与系统行为|disk_full|
|AISTD-69|验证测试后无隐藏错误|logs|
|AISTD-70|验证SSD的TRIM支持|trim|
|AISTD-174|验证随机读IOPS|fio|
|AISTD-209|验证系统识别全部存储设备且健康状态正常|disk_inventory|
|AISTD-210|验证磁盘IO性能符合设计预期|fio|
|AISTD-211|验证磁盘随机小块IO性能|fio|
|AISTD-212|验证SSD TRIM功能正常工作|trim|
|NEW-FILE|专用文件创建、复制、重命名及SHA256回读|file|
|NEW-RAW-READ|限定范围裸设备只读扫描|raw_read|

## 人工用例

|用例|内容|边界|
|---|---|---|
|AISTD-66|验证高温下磁盘稳定性|需要环境箱、实际断电、物理插拔或替换成员盘；本Skill不安排人工动作|
|AISTD-67|验证断电恢复能力|需要环境箱、实际断电、物理插拔或替换成员盘；本Skill不安排人工动作|
|AISTD-213|验证支持热插拔背板的Server SSD移除、插入与恢复|需要环境箱、实际断电、物理插拔或替换成员盘；本Skill不安排人工动作|
|AISTD-292|验证量产Server热插拔SSD移除、插入与恢复一致性|需要环境箱、实际断电、物理插拔或替换成员盘；本Skill不安排人工动作|
|AISTD-323|验证Server RAID/HBA与SSD故障、重建及数据完整性|需要环境箱、实际断电、物理插拔或替换成员盘；本Skill不安排人工动作|
|AISTD-326|验证量产Server RAID/HBA与SSD故障重建一致性|需要环境箱、实际断电、物理插拔或替换成员盘；本Skill不安排人工动作|

68隔离文件系统覆盖与真实业务分区盘满不同；212不从主机写入量推断NAND写放大；性能项仅记录数据。56需要真实boot ID变化，209批准驱动规格缺失时记录覆盖限制。原始步骤ID保留在jira-original.json及每次jira-coverage.json。
