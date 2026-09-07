# 问答与最新发布

问答应保持自然，不让用户先学习命令。先刷新本机范围，再运行以下形式：

```text
P S ask "当前安装了哪些 Skill？" --limit 20
P S ask "dashi-ppt 能做什么？依赖哪些工具？" --skill dashi-ppt
P S ask "当前安装的 PPT Skill 有哪些？" --limit 10
P S ask "最新发布的 Skill 工具有哪些？" --limit 10
```

`ask` 是索引查询和答案草稿。对某项详情，读取返回的 `read_entry_before_answering`，以实际 SKILL.md 的触发条件、工作流程、输出、依赖和排除条款为准。索引未填写的字段说“未记录”或“尚未核验”，不能推测。

## 最新发布

“最新”是时间敏感结论。先问索引；若 `requires_online_refresh` 为 true，才按需联网。优先使用作者/维护组织发布页、固定 release/tag、官方插件或技能目录。第三方目录和 GitHub Trending 可以提供候选，不能单独证明发布日期或兼容性。

每个对外发现的新条目需要作者或官方 Skill 文件、稳定来源链接、具体文件路径（仓库来源）、发布日期及其发布页。保存为候选记录时至少包含：

```json
{
  "name": "真实名称",
  "description": "经原始来源核验的用途",
  "source_url": "https://github.com/owner/repo",
  "source_path": "skills/example/SKILL.md",
  "source_ref": "固定 commit、tag 或 release",
  "release_published_at": "2026-09-07T08:00:00+00:00",
  "release_url": "https://github.com/owner/repo/releases/tag/v1.2.0",
  "source_checked_at": "2026-09-07T08:10:00+00:00",
  "discovered_at": "2026-09-07T08:10:00+00:00",
  "categories": ["coding"],
  "platforms": {"Codex": "unknown"}
}
```

用 `recruit <文件> --need "现有活跃库缺少的能力"` 写入候选区，之后再运行 ask。日期使用 ISO 8601 且包含时区；上游仅提供日期时使用该日的 `T00:00:00+00:00` 并在描述中说明精度。每次答复给出检索时间、每条发布日期和原始发布链接；范围表述为“本次已核验的候选”，不能说全网最新。

`latest_discovery_at` 只代表本地已发现条目最近一次记录时间，不能取代逐项 source_checked_at。未联网、联网不可用、搜索未找到合格项时，如实报告，不用未经核验的候选补齐名单。推荐或安装前仍要读取实际 SKILL.md，最新发布不代表安全、适配或已安装。
