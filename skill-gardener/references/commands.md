# 命令与数据

`P` 代表当前可用 Python 3.10+ 解释器，`S` 代表本技能 `scripts/gardener.py` 的绝对路径。下面是命令参数示意，不要把 P/S 当作实际程序名执行。所有命令输出 JSON。

```text
P S status
P S --library <绝对路径/library.sqlite3> status
P S scan --root <技能根目录> --root <另一技能根目录> --platform Codex
P S import-catalog <既有技能目录.json>
P S recruit <新候选.json> --need "现有活跃库的明确缺口"
P S compare --threshold 0.25
P S doctor
P S passport <skill-id>
P S duel <skill-a> <skill-b>
P S explore "制作一个产品发布会演示" --installed-only
P S promote <候选id> --reason "试用通过且具有独特价值"
P S upsert <审核通过的条目.json>
P S list --category presentations
P S list --favorites-only
P S list --state candidate
P S list --state archived
P S show <id>
P S ask "当前安装了哪些 Skill？" --limit 20
P S ask "dashi-ppt 能做什么？" --skill dashi-ppt
P S ask "最新发布的 Skill 工具有哪些？" --limit 10
P S recommend "制作一份中文产品汇报PPT" --category presentations --installed-only
P S recommend "处理Excel" --platform Codex --strict-platform --cost free --offline yes
P S recommend "制作封面" --tag illustration --exclude <id>
P S favorite <id> --reason "用户指定常用"
P S favorite <id> --remove --reason "用户取消收藏"
P S archive <id> --reason "用户指定出库"
P S restore <id> --reason "用户要求恢复"
P S annotate <id> <筛选字段补充.json> --reason "来源依据及实际判断"
P S preferences show
P S preferences set preferred_categories '["writing","design"]' --reason "用户明确偏好"
P S preferences set unused_days 90 --reason "用户指定闲置期限"
P S preferences unset cost --reason "用户取消永久预算限制"
P S maintain
P S maintain --unused-days 90 --candidate-days 30 --archive-unused --archive-stale-candidates --reason "用户要求整理闲置项和候选区"
P S history --skill <id>
P S export <新的导出文件.json>
```

问答的最新条目只来自已保存且具有 `release_published_at` 的作者/官方发布记录；出现 requires_online_refresh 时按 [问答与最新发布](questions.md) 刷新，不能把旧缓存当实时榜单。trial-start / trial-finish / record-use / feedback 见 [试用与养护](lifecycle.md)。Windows PowerShell 的 JSON 参数用单引号括起，字符串值本身仍需 JSON 双引号。例如 `preferences set platform '"Codex"' --reason '用户指定平台'`。传文件用于候选与字段更新，不将原始用户文字拼成可执行命令。

## 偏好

- 硬筛选默认：platform、cost、offline、difficulty、language。显式命令参数覆盖保存的值；platform=unknown 在普通推荐中会提示，只有 `--strict-platform` 才严格排除。
- 软偏好：preferred_categories、preferred_tags、preferred_skills。
- 排除：excluded_skills，可用精确 id 或名称；同名来源不明确时用 id。
- style 保存原文供宿主语义判断，不参与脚本硬筛选。
- unused_days / review_days 控制养护阈值，默认 90 / 30。

`preferences set` 必须有用户表达或授权依据；不从单次任务猜测永久偏好。用户的筛选若包含多个条件，逐一传入并向用户展示实际应用的限制。cost=free 只表示已核验该技能工作流的费用条件，不把开源许可证等同于免费 API 调用。

## 个人库

SQLite 事务保护并发修改。技能资料、个人状态、试用、偏好与事件分表保存；每条使用包含证据引用，个人记录不回写到公开目录。导出含个人记录，默认留在本地，不上传。数据库文件可整体备份/迁移；绑定支持不同目录使用同一库。

扫描的 `installed` 仅表示入口路径可读，脚本并不判断宿主启用状态。扫描不会写入平台兼容承诺。当前会话明示技能可用时，宿主可据此做实际选择，并把确切依据补充进 platforms/evidence_url/source_checked_at。不要仅凭安装目录标记实测。

自动解析支持常见 name/description 标量、单双引号和多行写法；特殊 YAML 语法需宿主读原文件后手动入库，扫描报告 warnings，不能忽略失败项。分类依据描述词汇，可能包含排除场景，因此检索后要读技能边界。

annotate 写入的个人标签/核验字段在重新扫描时保留；再次 annotate 可更新。外部来源身份固定，同名不同源需显式区分；推荐列表会折叠正文哈希相同的本地副本。
