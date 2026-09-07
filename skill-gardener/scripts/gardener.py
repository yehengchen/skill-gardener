#!/usr/bin/env python3
"""Personal skill index. Standard library only; never executes indexed skills."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from urllib.parse import urlsplit


CATEGORIES = {
    "presentations": "ppt pptx slides presentation presentations 幻灯片 演示文稿 演讲 汇报材料",
    "documents": "docx word document documents 文档 合同 报告 排版",
    "spreadsheets": "xlsx xls csv spreadsheet spreadsheets excel 表格 电子表格 数据分析",
    "pdf": "pdf 扫描件 合并pdf 拆分pdf",
    "design": "frontend interface ux ui design 网页 网站 界面 交互 设计",
    "image": "image images photo illustration 图片 图像 插画 海报 封面 绘图",
    "video": "video gif animation 视频 动图 剪辑",
    "audio": "audio music transcript transcription 音频 音乐 录音 听记 转录",
    "writing": "writing blog copywriting article caption captions xiaohongshu 内容 创作 文案 写作 长文 小红书 博客",
    "coding": "code coding debug test refactor 编程 代码 开发 调试 测试",
    "research": "research search discover 调研 研究 搜索 检索 找人",
    "productivity": "calendar todo task schedule 日程 待办 效率 会议室",
    "communication": "chat mail email message 消息 群聊 邮件 沟通",
    "cloud": "azure foundry deployment cloud 云端 部署 配额",
    "skill-management": "skill skills plugin 技能 插件",
}
ALIASES = {"content-creation": "writing", "creative-tools": "design",
           "development": "coding", "productivity-tools": "productivity"}
ENUMS = {
    "cost": {"free", "paid", "freemium", "unknown"},
    "offline": {"yes", "no", "unknown"},
    "difficulty": {"beginner", "intermediate", "advanced", "unknown"},
    "maintenance": {"active", "stable", "archived", "deprecated", "unavailable", "unknown"},
}
LIST_FIELDS = {"categories", "tags", "requires", "outputs", "language"}
META_FIELDS = {"name", "description", "source_url", "source_path", "source_ref", "source_checked_at",
               "last_upstream_update", "release_published_at", "release_url", "discovered_at",
               "evidence_url", "license", "platforms"} | LIST_FIELDS | set(ENUMS)
PREF_FIELDS = {"preferred_categories", "preferred_tags", "preferred_skills", "excluded_skills",
               "platform", "cost", "offline", "difficulty", "language", "style", "unused_days", "review_days"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
GENERIC_QUERY_WORDS = {
    "a", "an", "the", "i", "to", "for", "my", "me", "help", "use", "using", "skill", "skills",
    "want", "make", "create", "find", "recommend", "tool", "tools", "please", "need", "task",
}
GENERIC_CJK_BIGRAMS = {
    "帮我", "我想", "想要", "需要", "一个", "一下", "可以", "请帮", "给我", "这个", "那个", "这些", "那些",
    "如何", "怎么", "什么", "哪些", "是否", "能否", "目前", "现在", "相关", "工具", "技能", "功能", "需求",
    "任务", "用户", "推荐", "适合", "使用", "寻找", "查找", "制作", "处理", "完成", "实现", "帮忙",
}
NEGATIVE_SCOPE_MARKERS = (
    " do not use", " not for", " does not support", " unsupported", " excludes ",
    "不做", "不包括", "不适用", "不要用于", "不支持", "除外",
)
QUERY_ALIASES = {
    "小红书": {"xiaohongshu"},
    "图文": {"carousel"},
    "邮件": {"email", "mail"},
    "邮箱": {"email", "mail"},
    "部署": {"deploy", "deployment"},
    "模型": {"model", "models"},
    "合同": {"contract"},
    "演示文稿": {"presentation", "presentations", "slides"},
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def days_since(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def default_library():
    return Path(os.environ.get("SKILL_GARDENER_HOME", str(Path.home() / ".skill-gardener"))) / "library.sqlite3"


class Library:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=20)
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError("Unsupported library version; use a matching skill version")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS skills (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS preferences (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                skill_id TEXT, action TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trials (
                id TEXT PRIMARY KEY, skill_id TEXT NOT NULL REFERENCES skills(id), data TEXT NOT NULL);
            PRAGMA user_version=1;
        """)

    def all(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM skills ORDER BY id")]

    def resolve(self, value):
        row = self.db.execute("SELECT data FROM skills WHERE id=?", (value,)).fetchone()
        if row:
            return json.loads(row[0])
        matches = [s for s in self.all() if s["name"].casefold() == value.casefold()]
        if len(matches) != 1:
            raise ValueError("Skill absent or ambiguous; use an exact id from list: " + value)
        return matches[0]

    def save(self, item):
        self.db.execute("INSERT INTO skills VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (item["id"], dumps(item)))

    def event(self, action, data, skill_id=None):
        self.db.execute("INSERT INTO events(at,skill_id,action,data) VALUES (?,?,?,?)",
                        (now(), skill_id, action, dumps(data)))

    def prefs(self):
        row = self.db.execute("SELECT data FROM preferences WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}

    def upsert(self, meta, *, local=False):
        validate_meta(meta, local=local)
        ident = meta.get("identity") or ((meta.get("source_url", "").rstrip("/") + "#" + meta["source_path"])
                                        if meta.get("source_path") else meta.get("source_url"))
        if not ident:
            raise ValueError("A source_url is required for external entries")
        key = "sk-" + hashlib.sha256(ident.encode()).hexdigest()[:16]
        row = self.db.execute("SELECT data FROM skills WHERE id=?", (key,)).fetchone()
        prior = json.loads(row[0]) if row else None
        item = prior or {"id": key, "identity": ident, "state": "active", "favorite": False,
                         "created_at": now(), "last_used_at": None, "successes": 0,
                         "failures": 0, "consecutive_failures": 0, "ratings": [], "overrides": {}}
        for field, value in meta.items():
            if field in META_FIELDS or (local and field in {"local_path", "scan_root", "content_hash", "present_on"}):
                item[field] = value
        for field, choices in ENUMS.items():
            item.setdefault(field, "unknown")
        for field in LIST_FIELDS:
            item.setdefault(field, [])
        item.setdefault("platforms", {})
        item.setdefault("local_path", None)
        if not local:
            item.setdefault("discovered_at", now())
        item["indexed_at"] = now()
        item.update(item["overrides"])
        self.save(item)
        self.event("refresh" if prior else "discover", {"source": ident}, key)
        return item


def validate_meta(meta, local=False, partial=False):
    if not isinstance(meta, dict):
        raise ValueError("Skill record must be an object")
    if not partial:
        for field in ("name", "description"):
            if not isinstance(meta.get(field), str) or not meta[field].strip():
                raise ValueError("Missing nonempty " + field)
    unknown = set(meta) - META_FIELDS - ({"identity", "local_path", "scan_root", "content_hash", "present_on"} if local else set())
    if unknown:
        raise ValueError("Unsupported record fields: " + ", ".join(sorted(unknown)))
    for field in META_FIELDS - LIST_FIELDS - set(ENUMS) - {"platforms"}:
        if field in meta and meta[field] is not None and not isinstance(meta[field], str):
            raise ValueError(field + " must be a string or null")
    for field in ("name", "description"):
        if field in meta and (not isinstance(meta[field], str) or not meta[field].strip()):
            raise ValueError(field + " must be a nonempty string")
    for field, choices in ENUMS.items():
        if field in meta and meta[field] not in choices:
            raise ValueError("Invalid " + field)
    for field in LIST_FIELDS:
        if field in meta and (not isinstance(meta[field], list) or not all(isinstance(v, str) for v in meta[field])):
            raise ValueError(field + " must be a string array")
    for field in ("source_url", "release_url", "evidence_url"):
        if meta.get(field):
            url = urlsplit(meta[field])
            if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
                raise ValueError(field + " requires an http(s) URL without credentials")
    for field in ("source_checked_at", "last_upstream_update", "release_published_at", "discovered_at"):
        if meta.get(field):
            days_since(meta[field])
    platforms = meta.get("platforms", {})
    if not isinstance(platforms, dict) or any(not isinstance(k, str) or v not in {"supported", "unsupported", "unknown"} for k, v in platforms.items()):
        raise ValueError("platforms must map platform names to supported/unsupported/unknown")


def contains(text, word):
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text)) if word.isascii() else word in text


def categories(text):
    text = text.casefold()
    return [k for k, words in CATEGORIES.items() if any(contains(text, word) for word in words.split())]


def positive_scope(text):
    """Keep capability claims while excluding common negative-scope clauses from retrieval."""
    folded = " " + text.casefold()
    cuts = [folded.find(marker) for marker in NEGATIVE_SCOPE_MARKERS if folded.find(marker) >= 0]
    return text[:max(0, min(cuts) - 1)] if cuts else text


def frontmatter(path):
    text = path.read_text(encoding="utf-8-sig")
    found = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not found:
        raise ValueError("Missing YAML frontmatter")
    meta = {}
    lines = found.group(1).splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"^(name|description):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if value in ("|", ">", "|-", ">-", "|+", ">+") or not value:
            extra = []
            for following in lines[i + 1:]:
                if following and not following[0].isspace():
                    break
                extra.append(following.strip())
            value = " ".join(extra)
        elif value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        else:
            value = re.split(r"\s+#", value)[0]
        meta[key] = value
    if not meta.get("name") or not meta.get("description"):
        raise ValueError("Missing name or description")
    meta["categories"] = categories(meta["name"] + " " + positive_scope(meta["description"]))
    meta["content_hash"] = hashlib.sha256(text.encode()).hexdigest()
    return meta


def scan(lib, roots, platform):
    seen, warnings, count = set(), [], 0
    for raw in roots:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            warnings.append({"path": str(root), "error": "Root unavailable"})
            continue
        def walk_error(error):
            warnings.append({"path": error.filename, "error": str(error)})
        for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not Path(folder, d).is_symlink())
            if "SKILL.md" not in files:
                continue
            path = (Path(folder) / "SKILL.md").resolve()
            if path in seen:
                continue
            seen.add(path)
            try:
                if path.stat().st_size > 512_000:
                    raise ValueError("Entry exceeds 512 KB")
                meta = frontmatter(path)
                meta.update(identity="local:" + os.path.normcase(str(path)), local_path=str(path),
                            scan_root=str(root), present_on=[platform] if platform else [])
                lib.upsert(meta, local=True)
                count += 1
            except (OSError, UnicodeError, ValueError) as error:
                warnings.append({"path": str(path), "error": str(error)})
    return {"scanned": count, "roots": roots, "warnings": warnings,
            "note": "Local presence is not activation or runtime verification."}


def import_catalog(lib, path):
    records = read_json(path)
    if not isinstance(records, list):
        raise ValueError("Catalog must be a JSON array")
    ids, skipped = [], []
    for record in records:
        if record.get("type", "agent_skill") != "agent_skill":
            skipped.append({"name": record.get("name"), "reason": "Not an Agent Skill"})
            continue
        source = record.get("source", {})
        file = record.get("file_evidence", {})
        url = source.get("canonical_url", "").rstrip("/")
        upstream = file.get("upstream_path")
        platforms = {}
        for p in record.get("platforms", []):
            supported = p.get("claim_status") in {"publisher_explicit", "platform_official_explicit"}
            platforms[p["name"]] = "supported" if supported else "unknown"
        meta = {"name": record["name"],
                "description": " ".join(filter(None, [record.get("tagline"), record.get("summary")])),
                "source_url": url, "source_path": upstream,
                "source_ref": source.get("commit") or source.get("tag") or "",
                "evidence_url": file.get("immutable_url") or source.get("canonical_url"),
                "source_checked_at": source.get("checked_at"), "license": source.get("license"),
                "platforms": platforms,
                "categories": sorted(set(ALIASES.get(c, c) for c in record.get("categories", []))),
                "difficulty": record.get("difficulty") if record.get("difficulty") in ENUMS["difficulty"] else "unknown",
                "maintenance": source.get("repo_status") if source.get("repo_status") in ENUMS["maintenance"] else "unknown"}
        ids.append(lib.upsert(meta)["id"])
    return {"imported": len(ids), "ids": ids, "skipped": skipped}


def installed(item):
    return bool(item.get("local_path") and Path(item["local_path"]).is_file())


def platform_status(item, platform):
    return next((v for k, v in item.get("platforms", {}).items() if k.casefold() == platform.casefold()), "unknown")


def tokens(query):
    words = set(re.findall(r"[a-z0-9][a-z0-9._-]*", query.casefold()))
    words -= GENERIC_QUERY_WORDS
    for chunk in re.findall(r"[\u4e00-\u9fff]+", query):
        words.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return words - GENERIC_CJK_BIGRAMS


def query_aliases(query):
    folded = query.casefold()
    return {alias for cue, aliases in QUERY_ALIASES.items() if cue in folded for alias in aliases}


def recommend(lib, args):
    prefs = lib.prefs()
    query = args.query.casefold().strip()
    if not query:
        raise ValueError("A nonempty task is required")
    qtokens = tokens(query)
    qaliases = query_aliases(query)
    qcats = set(categories(query))
    if not re.search(r"(创建|安装|管理|编写|制作|整理|create|install|manage|write)\s*(一个|个|a|an|my)?\s*(skill|技能|plugin|插件)", query):
        qcats.discard("skill-management")
    if args.category:
        qcats.add(args.category)
    results, excluded = [], Counter()
    constraints = {k: getattr(args, k, None) or prefs.get(k) for k in ("platform", "cost", "offline", "difficulty", "language")}
    omit = set(prefs.get("excluded_skills", [])) | set(args.exclude or [])
    for item in lib.all():
        sid, name = item["id"], item["name"]
        if item["state"] != "active" or name == "skill-gardener" or sid in omit or name in omit:
            excluded["not_active_or_excluded"] += 1
            continue
        if args.favorites_only and not item["favorite"]:
            excluded["not_favorite"] += 1
            continue
        if args.installed_only and not installed(item):
            excluded["not_local"] += 1
            continue
        if args.category and args.category not in item["categories"]:
            excluded["category"] += 1
            continue
        if args.tag and not set(args.tag).issubset(item["tags"]):
            excluded["tags"] += 1
            continue
        rejected = False
        for key in ("cost", "offline", "difficulty"):
            if constraints[key] and item.get(key) != constraints[key]:
                excluded[key + "_mismatch_or_unknown"] += 1
                rejected = True
        if constraints["language"] and constraints["language"] not in item.get("language", []):
            excluded["language_mismatch_or_unknown"] += 1
            rejected = True
        pstatus = platform_status(item, constraints["platform"]) if constraints["platform"] else None
        if pstatus == "unsupported" or (args.strict_platform and pstatus != "supported"):
            excluded["platform_mismatch_or_unknown"] += 1
            rejected = True
        if rejected:
            continue
        haystack = " ".join([name, positive_scope(item["description"])] + item["tags"] + item["outputs"]).casefold()
        matched = sorted(t for t in qtokens if contains(haystack, t))
        matched_aliases = sorted(t for t in qaliases if contains(haystack, t))
        catmatch = sorted(qcats & set(item["categories"]))
        direct = contains(query, name.casefold())
        if not (matched or matched_aliases or catmatch or direct):
            excluded["no_task_match"] += 1
            continue
        category_complete = bool(qcats) and qcats.issubset(set(item["categories"]))
        strong_lexical = bool(matched_aliases) or (bool(matched) and (len(qtokens) == 1 or len(matched) >= 2))
        if len(qcats) > 1 and not category_complete and not direct:
            excluded["partial_category_coverage"] += 1
            continue
        broad = bool(getattr(args, "broad", False))
        if direct or strong_lexical:
            evidence_level = "high"
        elif category_complete or (args.category and catmatch):
            evidence_level = "medium"
        elif broad:
            evidence_level = "low"
        else:
            excluded["weak_task_match"] += 1
            continue
        semantic = min(1.0, (len(matched) + len(matched_aliases)) / max(1, len(qtokens) + len(qaliases)))
        category_fit = len(catmatch) / max(1, len(qcats))
        evidence_bonus = {"high": 30, "medium": 10, "low": 0}[evidence_level]
        base = 65 * semantic + 25 * category_fit + (60 if direct else 0) + evidence_bonus
        personal = (7 if item["favorite"] else 0) + min(6, item["successes"] * 1.5)
        if item["ratings"]:
            personal += (sum(item["ratings"]) / len(item["ratings"]) - 3) * 2
        if sid in prefs.get("preferred_skills", []) or name in prefs.get("preferred_skills", []):
            personal += 5
        personal += min(3, len(set(item["categories"]) & set(prefs.get("preferred_categories", []))))
        personal += min(3, len(set(item["tags"]) & set(prefs.get("preferred_tags", []))))
        warnings = []
        if pstatus == "unknown":
            warnings.append("Platform compatibility unconfirmed")
        if not installed(item):
            warnings.append("No readable local entry; obtain/review before invocation")
        if item["maintenance"] in {"archived", "deprecated", "unavailable"}:
            warnings.append("Upstream status: " + item["maintenance"])
            personal -= 10
        if item["failures"]:
            personal -= min(8, item["consecutive_failures"] * 2)
        # Personal history cannot promote a very weak match over a strong match.
        score = base + max(-15, min(15, personal)) * min(1, base / 30)
        results.append({"id": sid, "name": name, "score": round(score, 2), "task_score": round(base, 2),
                        "description": item["description"], "categories": item["categories"],
                        "matched_terms": matched, "matched_aliases": matched_aliases, "matched_categories": catmatch,
                        "evidence_level": evidence_level,
                        "match_reason": (["exact_name"] if direct else []) +
                                        (["task_terms"] if strong_lexical else []) +
                                        (["complete_category_coverage"] if category_complete else []),
                        "favorite": item["favorite"], "successes": item["successes"],
                        "entry": item.get("local_path") if installed(item) else item.get("evidence_url") or item.get("source_url"),
                        "installed": installed(item), "platform_status": pstatus,
                        "warnings": warnings, "content_hash": item.get("content_hash")})
    evidence_rank = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda s: (evidence_rank[s["evidence_level"]], -s["score"], -int(s["installed"]), s["id"]))
    unique, hashes = [], set()
    for item in results:
        digest = item.pop("content_hash")
        if digest and digest in hashes:
            continue
        if digest:
            hashes.add(digest)
        unique.append(item)
    broad = bool(getattr(args, "broad", False))
    high = [item for item in unique if item["evidence_level"] == "high"]
    qualified = unique if broad or not high else high
    if high and not broad:
        excluded["lower_evidence_than_best"] += len(unique) - len(high)
    return {"query": args.query, "constraints": constraints, "total_matches": len(qualified),
            "results": qualified[:args.limit], "excluded": dict(excluded),
            "reference_only": True,
            "note": "Reference shortlist only. Read the actual skill entry and verify task fit before routing or execution."}


def compact_detail(item):
    """Return the facts needed for an answer without presenting stale local paths as usable."""
    local = installed(item)
    return {
        "id": item["id"], "name": item["name"], "description": item["description"],
        "installed": local, "entry": item.get("local_path") if local else None,
        "source_url": item.get("source_url"), "source_path": item.get("source_path"),
        "source_ref": item.get("source_ref"), "evidence_url": item.get("evidence_url"),
        "categories": item.get("categories", []), "tags": item.get("tags", []),
        "outputs": item.get("outputs", []), "requires": item.get("requires", []),
        "platforms": item.get("platforms", {}), "cost": item.get("cost"),
        "offline": item.get("offline"), "difficulty": item.get("difficulty"),
        "maintenance": item.get("maintenance"), "favorite": item["favorite"],
        "state": item["state"], "candidate_need": item.get("candidate_need"),
        "candidate_since": item.get("candidate_since"),
        "personal_usage": {"successes": item["successes"], "failures": item["failures"],
                           "last_used_at": item["last_used_at"], "ratings": item["ratings"]},
        "source_checked_at": item.get("source_checked_at"),
        "read_entry_before_answering": item.get("local_path") if local else item.get("evidence_url"),
    }


def question(lib, args):
    query = args.question.strip()
    if not query:
        raise ValueError("A nonempty question is required")
    text = query.casefold()
    wants_latest = any(word in text for word in ("最新", "新发布", "最近发布", "最新发布", "latest", "recent"))
    wants_installed = any(word in text for word in ("当前", "已安装", "本机", "本地", "installed", "local"))
    detail_words = ("详细", "详情", "介绍", "能做什么", "怎么用", "依赖", "detail", "about", "what does")
    requested = lib.resolve(args.skill) if args.skill else None
    if wants_latest:
        entries = [s for s in lib.all() if s["state"] in {"active", "candidate"} and s.get("release_published_at")]
        entries.sort(key=lambda s: (s["release_published_at"], s.get("discovered_at", "")), reverse=True)
        latest = []
        for item in entries[:args.limit]:
            latest.append({"id": item["id"], "name": item["name"], "description": item["description"],
                           "release_published_at": item["release_published_at"], "release_url": item.get("release_url"),
                           "source_url": item.get("source_url"), "source_ref": item.get("source_ref"),
                           "source_checked_at": item.get("source_checked_at"), "installed": installed(item),
                           "state": item["state"]})
        freshness = max((s.get("discovered_at", "") for s in entries), default=None)
        return {"kind": "latest_published", "question": query, "items": latest,
                "answer": "已按作者或官方发布日排序。" if latest else "本地索引没有带发布日期的已核验新条目；需要按需联网发现后再回答最新列表。",
                "latest_discovery_at": freshness, "requires_online_refresh": not bool(latest) or days_since(freshness) not in (0,),
                "scope": "Only active or candidate items with a verified release_published_at in this personal library; this is not a claim to cover every marketplace."}
    if requested:
        return {"kind": "skill_detail", "question": query, "answer": "已找到指定 Skill。读取入口文件后可回答具体工作流和排除条件。",
                "item": compact_detail(requested)}
    named = [s for s in lib.all() if s["name"].casefold() in text]
    if len(named) == 1:
        return {"kind": "skill_detail", "question": query, "answer": "已匹配到一个 Skill。读取入口文件后可回答具体工作流和排除条件。",
                "item": compact_detail(named[0])}
    if wants_installed and (any(word in text for word in detail_words) or named):
        available = [s for s in lib.all() if installed(s)]
        candidates = [s for s in available if not named or s in named]
        return {"kind": "installed_candidates", "question": query,
                "answer": "找到多个可能的本机 Skill；请用 id 或完整名称追问其中一个的详情。",
                "items": [compact_detail(s) for s in candidates[:args.limit]]}
    if wants_installed:
        available = [s for s in lib.all() if installed(s)]
        return {"kind": "installed_overview", "question": query,
                "answer": "当前可读取的本地 Skill 如下；它们是否自动启用仍由宿主决定。",
                "count": len(available), "by_category": dict(Counter(c for s in available for c in s["categories"])),
                "items": [{"id": s["id"], "name": s["name"], "description": s["description"],
                           "entry": s["local_path"]} for s in available[:args.limit]]}
    suggestions = recommend(lib, argparse.Namespace(query=query, category=None, tag=None, platform=None,
                            strict_platform=False, cost=None, offline=None, difficulty=None, language=None,
                            favorites_only=False, installed_only=False, exclude=None, limit=args.limit))
    return {"kind": "skill_search", "question": query, "answer": "按问题找到了以下候选；回答详情前应读取实际入口文件。",
            "items": suggestions["results"], "total_matches": suggestions["total_matches"]}


def recruit(lib, args):
    """Stage externally discovered skills until they earn a place in the active library."""
    records = read_json(args.file)
    if not isinstance(records, list):
        records = [records]
    recruited, already_active = [], []
    for meta in records:
        identity = meta.get("identity") or ((meta.get("source_url", "").rstrip("/") + "#" + meta["source_path"])
                                         if meta.get("source_path") else meta.get("source_url"))
        existing = next((s for s in lib.all() if s.get("identity") == identity), None)
        item = lib.upsert(meta)
        if existing and existing["state"] == "active":
            already_active.append(item["id"])
            continue
        item["state"] = "candidate"
        item["candidate_since"] = item.get("candidate_since") or now()
        item["candidate_need"] = args.need
        lib.save(item)
        lib.event("recruit", {"need": args.need, "source": item.get("source_url")}, item["id"])
        recruited.append(item["id"])
    return {"candidate_ids": recruited, "already_active_ids": already_active,
            "next": "Review source, compare overlap, and run a real trial before promotion."}


def comparison_terms(item):
    text = " ".join([item["name"], item["description"]] + item.get("tags", []) + item.get("outputs", [])).casefold()
    terms = set(re.findall(r"[a-z0-9][a-z0-9._-]*", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(run[i:i + 2] for i in range(max(0, len(run) - 1)))
    return terms


def compare(lib, args):
    """Suggest pairs for human review; similarity is never automatic deletion or merging."""
    if not 0 <= args.threshold <= 1:
        raise ValueError("Comparison threshold must be between 0 and 1")
    items = [s for s in lib.all() if s["state"] in {"active", "candidate"}]
    pairs = []
    for position, left in enumerate(items):
        left_terms = comparison_terms(left)
        for right in items[position + 1:]:
            if left.get("identity") == right.get("identity"):
                continue
            right_terms = comparison_terms(right)
            lexical = len(left_terms & right_terms) / max(1, len(left_terms | right_terms))
            shared_categories = sorted(set(left["categories"]) & set(right["categories"]))
            same_name = left["name"].casefold() == right["name"].casefold()
            if not same_name and (not shared_categories or lexical < args.threshold):
                continue
            pairs.append({"left": {"id": left["id"], "name": left["name"], "state": left["state"]},
                          "right": {"id": right["id"], "name": right["name"], "state": right["state"]},
                          "same_name": same_name, "shared_categories": shared_categories,
                          "term_overlap": round(lexical, 2),
                          "action": "Compare actual SKILL.md files and outcomes; keep both, promote one, or archive one with a reason."})
    pairs.sort(key=lambda pair: (-int(pair["same_name"]), -pair["term_overlap"], pair["left"]["id"], pair["right"]["id"]))
    return {"pairs": pairs[:args.limit], "total_pairs": len(pairs), "threshold": args.threshold,
            "note": "This is a review queue, not evidence that either skill is redundant."}


def passport(lib, args):
    """Create an evidence-backed card for one skill without inventing strengths."""
    item = lib.resolve(args.skill)
    trials = [json.loads(row[0]) for row in lib.db.execute(
        "SELECT data FROM trials WHERE skill_id=? ORDER BY id", (item["id"],))]
    outcomes = Counter(trial.get("outcome", "unknown") for trial in trials)
    warnings = []
    if not installed(item):
        warnings.append("No readable local entry")
    if not item.get("source_checked_at"):
        warnings.append("Source has not been checked")
    if item.get("maintenance") in {"archived", "deprecated", "unavailable"}:
        warnings.append("Upstream status: " + item["maintenance"])
    if not trials and not item["successes"]:
        warnings.append("No personal task evidence yet")
    return {"kind": "skill_passport", "skill": compact_detail(item),
            "trial_summary": dict(outcomes), "recent_trials": trials[-args.limit:],
            "verified_strengths": [trial["task"] for trial in trials if trial.get("outcome") == "success"],
            "warnings": warnings,
            "note": "Strengths come only from successful personal trials; the description is publisher or local metadata."}


def duel(lib, args):
    """Compare homogeneous skills using their local facts and personal evidence."""
    items = [lib.resolve(value) for value in args.skills]
    if len({item["id"] for item in items}) < 2:
        raise ValueError("A duel requires at least two different skills")
    rows = []
    for item in items:
        trials = [json.loads(row[0]) for row in lib.db.execute(
            "SELECT data FROM trials WHERE skill_id=?", (item["id"],))]
        outcomes = Counter(trial.get("outcome", "unknown") for trial in trials)
        ratings = item.get("ratings", [])
        rows.append({"id": item["id"], "name": item["name"], "state": item["state"],
                     "installed": installed(item), "categories": item.get("categories", []),
                     "outputs": item.get("outputs", []), "requires": item.get("requires", []),
                     "cost": item.get("cost"), "offline": item.get("offline"),
                     "successes": item["successes"], "failures": item["failures"],
                     "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                     "trial_outcomes": dict(outcomes), "source_checked_at": item.get("source_checked_at")})
    evidence_order = sorted(rows, key=lambda row: (-row["successes"], row["failures"],
                            -(row["average_rating"] or 0), row["id"]))
    leader = evidence_order[0] if evidence_order[0]["successes"] > evidence_order[1]["successes"] else None
    return {"kind": "skill_duel", "skills": rows,
            "evidence_leader": {"id": leader["id"], "name": leader["name"]} if leader else None,
            "next_trial": "Run the same small task and acceptance criteria with each skill when evidence is insufficient or tied.",
            "note": "This compares recorded evidence; it does not declare a universally better skill."}


def explore(lib, args):
    """Offer one relevant but under-tried active skill to prevent preference lock-in."""
    shortlist = recommend(lib, argparse.Namespace(
        query=args.query, category=args.category, tag=None, platform=args.platform,
        strict_platform=args.strict_platform, cost=None, offline=None, difficulty=None,
        language=None, favorites_only=False, installed_only=args.installed_only,
        exclude=None, limit=50))
    by_id = {item["id"]: item for item in lib.all()}
    choices = shortlist["results"]
    choices.sort(key=lambda row: (
        by_id[row["id"]]["successes"] > 0,
        by_id[row["id"]]["favorite"],
        by_id[row["id"]].get("last_used_at") or "",
        -row["task_score"], row["id"]))
    selected = choices[0] if choices else None
    return {"kind": "exploration_slot", "query": args.query, "selected": selected,
            "reason": "Relevant to the task and has little or no personal usage evidence." if selected else "No relevant active skill was found.",
            "guardrail": "Exploration never overrides hard constraints and does not recruit or install anything."}


def doctor(lib, args):
    """Give the user a compact portfolio health report and an actionable queue."""
    items = lib.all()
    active = [item for item in items if item["state"] == "active"]
    candidates = [item for item in items if item["state"] == "candidate"]
    category_counts = Counter(category for item in active for category in item.get("categories", []))
    preferred = lib.prefs().get("preferred_categories", [])
    maintenance = maintain(lib, argparse.Namespace(
        unused_days=args.unused_days, review_days=args.review_days, candidate_days=args.candidate_days,
        archive_unused=False, archive_stale_candidates=False, reason=None))
    overlap = compare(lib, argparse.Namespace(threshold=args.threshold, limit=args.limit))
    missing = sorted(category for category in preferred if category_counts[category] == 0)
    crowded = [{"category": category, "active_count": count}
               for category, count in sorted(category_counts.items()) if count >= args.crowded_at]
    actions = []
    if candidates:
        actions.append({"priority": 1, "action": "review_candidates", "count": len(candidates)})
    if overlap["total_pairs"]:
        actions.append({"priority": 2, "action": "compare_overlap", "count": overlap["total_pairs"]})
    if missing:
        actions.append({"priority": 3, "action": "investigate_coverage_gaps", "categories": missing})
    due = [row for row in maintenance["findings"] if row["reasons"] != ["candidate_pending_trial"]]
    if due:
        actions.append({"priority": 4, "action": "maintenance_review", "count": len(due)})
    return {"kind": "skill_library_health", "counts": {
                "total": len(items), "active": len(active), "candidate": len(candidates),
                "archived": sum(item["state"] == "archived" for item in items),
                "favorites": sum(item["favorite"] for item in items),
                "readable_local": sum(installed(item) for item in items)},
            "active_by_category": dict(category_counts), "preferred_category_gaps": missing,
            "crowded_categories": crowded, "overlap_review": overlap["pairs"],
            "maintenance_review": maintenance["findings"][:args.limit], "next_actions": actions,
            "note": "Crowding and overlap are review signals, not instructions to remove skills."}


def record_result(lib, item, outcome, evidence, note, rating=None):
    if outcome == "success" and not evidence:
        raise ValueError("Success requires an actual artifact or result reference")
    if outcome != "blocked":
        item["last_used_at"] = now()
    if outcome == "success":
        item["successes"] += 1
        item["consecutive_failures"] = 0
    elif outcome == "failed":
        item["failures"] += 1
        item["consecutive_failures"] += 1
    if rating is not None:
        item["ratings"].append(rating)
    lib.save(item)
    lib.event("use", {"outcome": outcome, "evidence": evidence, "note": note, "rating": rating}, item["id"])


def trial_start(lib, args):
    item = lib.resolve(args.skill)
    trial = {"id": "trial-" + uuid.uuid4().hex[:16], "skill_id": item["id"], "task": args.task,
             "criterion": args.criterion, "platform": args.platform, "started_at": now(),
             "source_ref": item.get("source_ref"), "content_hash": item.get("content_hash"), "outcome": "pending"}
    lib.db.execute("INSERT INTO trials VALUES (?,?,?)", (trial["id"], item["id"], dumps(trial)))
    lib.event("trial-start", trial, item["id"])
    return trial


def trial_finish(lib, args):
    row = lib.db.execute("SELECT data FROM trials WHERE id=?", (args.trial,)).fetchone()
    if not row:
        raise ValueError("Unknown trial id")
    trial = json.loads(row[0])
    if trial["outcome"] != "pending":
        raise ValueError("Trial already finished; refusing duplicate usage")
    item = lib.resolve(trial["skill_id"])
    record_result(lib, item, args.outcome, args.evidence, args.note, args.rating)
    trial.update(outcome=args.outcome, evidence=args.evidence, note=args.note, rating=args.rating, finished_at=now())
    lib.db.execute("UPDATE trials SET data=? WHERE id=?", (dumps(trial), args.trial))
    lib.event("trial-finish", trial, item["id"])
    return trial


def state_change(lib, args):
    item = lib.resolve(args.skill)
    before = {"state": item["state"], "favorite": item["favorite"]}
    if args.command == "favorite":
        item["favorite"] = not args.remove
    elif args.command == "promote":
        item["state"] = "active"
    elif args.command == "archive":
        if item["state"] != "archived":
            item["archived_from"] = item["state"]
        item["state"] = "archived"
    else:
        item["state"] = item.pop("archived_from", "active")
    after = {"state": item["state"], "favorite": item["favorite"]}
    if before != after:
        lib.save(item)
        lib.event(args.command, {"before": before, "after": after, "reason": args.reason}, item["id"])
    return {"id": item["id"], **after, "changed": before != after}


def maintain(lib, args):
    prefs = lib.prefs()
    unused_days = args.unused_days if args.unused_days is not None else prefs.get("unused_days", 90)
    review_days = args.review_days if args.review_days is not None else prefs.get("review_days", 30)
    candidate_days = args.candidate_days
    if unused_days < 1 or review_days < 1 or candidate_days < 1:
        raise ValueError("Day thresholds must be positive")
    if (args.archive_unused or args.archive_stale_candidates) and not args.reason:
        raise ValueError("Archiving requires a reason describing the user's request")
    findings, archived = [], []
    items = lib.all()
    names = Counter(s["name"] for s in items if s["state"] == "active")
    for item in items:
        if item["state"] == "archived":
            continue
        reasons = []
        if item["state"] == "candidate":
            stale = days_since(item.get("candidate_since") or item["created_at"]) >= candidate_days
            if stale:
                reasons.append("candidate_review_due")
            if item["maintenance"] in {"archived", "deprecated", "unavailable"}:
                reasons.append("upstream_" + item["maintenance"])
            findings.append({"id": item["id"], "name": item["name"], "state": "candidate", "favorite": item["favorite"],
                             "reasons": reasons or ["candidate_pending_trial"], "archive_eligible": stale and not item["favorite"]})
            if args.archive_stale_candidates and stale and not item["favorite"]:
                item["archived_from"] = "candidate"
                item["state"] = "archived"
                lib.save(item)
                lib.event("archive", {"reason": args.reason, "rule": "stale_candidate", "threshold_days": candidate_days}, item["id"])
                archived.append(item["id"])
            continue
        unused = days_since(item.get("last_used_at") or item["created_at"]) >= unused_days
        if unused:
            reasons.append("unused")
        checked = days_since(item.get("source_checked_at"))
        if checked is None:
            reasons.append("source_not_checked")
        elif checked >= review_days:
            reasons.append("source_review_due")
        if item["maintenance"] in {"archived", "deprecated", "unavailable"}:
            reasons.append("upstream_" + item["maintenance"])
        if item["consecutive_failures"] >= 3:
            reasons.append("repeated_failures")
        if item.get("local_path") and not installed(item):
            reasons.append("local_entry_missing")
        if names[item["name"]] > 1:
            reasons.append("same_name_review")
        if reasons:
            findings.append({"id": item["id"], "name": item["name"], "state": "active", "favorite": item["favorite"],
                             "reasons": reasons, "archive_eligible": unused and not item["favorite"]})
        if args.archive_unused and unused and not item["favorite"]:
            item["archived_from"] = "active"
            item["state"] = "archived"
            lib.save(item)
            lib.event("archive", {"reason": args.reason, "rule": "unused", "threshold_days": unused_days}, item["id"])
            archived.append(item["id"])
    return {"checked_at": now(), "unused_days": unused_days, "review_days": review_days, "candidate_days": candidate_days,
            "findings": findings, "archived": archived, "upstream_network_checked": False}


def preferences(lib, args):
    if args.operation == "show":
        return lib.prefs()
    if args.key not in PREF_FIELDS:
        raise ValueError("Unsupported preference: " + args.key)
    prefs = lib.prefs()
    before = prefs.get(args.key)
    if args.operation == "unset":
        prefs.pop(args.key, None)
        value = None
    else:
        value = json.loads(args.value)
        if args.key in {"preferred_categories", "preferred_tags", "preferred_skills", "excluded_skills"}:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError("This preference needs a JSON array of strings")
        elif args.key in {"unused_days", "review_days"}:
            if type(value) is not int or value < 1:
                raise ValueError("This preference needs a positive integer")
        elif not isinstance(value, str):
            raise ValueError("This preference needs a JSON string")
        if args.key in ENUMS and value not in ENUMS[args.key]:
            raise ValueError("Invalid preference value")
        prefs[args.key] = value
    lib.db.execute("INSERT INTO preferences VALUES (1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (dumps(prefs),))
    lib.event("preferences", {"key": args.key, "before": before, "after": value, "reason": args.reason})
    return prefs


def dispatch(lib, args):
    cmd = args.command
    if cmd == "status":
        items = lib.all()
        return {"library_path": str(lib.path), "schema_version": 1, "total": len(items),
                "active": sum(s["state"] == "active" for s in items),
                "candidates": sum(s["state"] == "candidate" for s in items),
                "archived": sum(s["state"] == "archived" for s in items),
                "favorites": sum(s["favorite"] for s in items), "readable_local": sum(installed(s) for s in items),
                "preferences": lib.prefs(), "categories": dict(Counter(c for s in items for c in s["categories"]))}
    if cmd == "scan":
        return scan(lib, args.root, args.platform)
    if cmd == "import-catalog":
        return import_catalog(lib, args.file)
    if cmd == "upsert":
        data = read_json(args.file)
        return [lib.upsert(s) for s in data] if isinstance(data, list) else lib.upsert(data)
    if cmd == "recruit":
        return recruit(lib, args)
    if cmd == "annotate":
        item = lib.resolve(args.skill)
        patch = read_json(args.file)
        validate_meta(patch, partial=True)
        if set(patch) & {"source_url", "source_path", "name"}:
            raise ValueError("Identity fields cannot be annotated; import a separate source")
        item["overrides"].update(patch)
        item.update(patch)
        lib.save(item)
        lib.event("annotate", {"fields": patch, "reason": args.reason}, item["id"])
        return item
    if cmd == "show":
        item = lib.resolve(args.skill)
        return {**item, "installed": installed(item)}
    if cmd == "ask":
        return question(lib, args)
    if cmd == "list":
        result = []
        for s in lib.all():
            if args.state != "all" and s["state"] != args.state:
                continue
            if args.category and args.category not in s["categories"]:
                continue
            if args.favorites_only and not s["favorite"]:
                continue
            if args.installed_only and not installed(s):
                continue
            result.append({k: s[k] for k in ("id", "name", "state", "favorite", "categories")})
        return result
    if cmd == "recommend":
        if args.strict_platform and not (args.platform or lib.prefs().get("platform")):
            raise ValueError("--strict-platform requires a platform argument or saved preference")
        return recommend(lib, args)
    if cmd in {"favorite", "archive", "restore", "promote"}:
        return state_change(lib, args)
    if cmd == "compare":
        return compare(lib, args)
    if cmd == "doctor":
        return doctor(lib, args)
    if cmd == "passport":
        return passport(lib, args)
    if cmd == "duel":
        return duel(lib, args)
    if cmd == "explore":
        return explore(lib, args)
    if cmd == "trial-start":
        return trial_start(lib, args)
    if cmd == "trial-finish":
        return trial_finish(lib, args)
    if cmd == "record-use":
        item = lib.resolve(args.skill)
        record_result(lib, item, args.outcome, args.evidence, args.note, args.rating)
        lib.event("usage-context", {"platform": args.platform, "source_ref": item.get("source_ref"),
                                    "content_hash": item.get("content_hash")}, item["id"])
        return {"id": item["id"], "successes": item["successes"], "failures": item["failures"]}
    if cmd == "feedback":
        item = lib.resolve(args.skill)
        item["ratings"].append(args.rating)
        lib.save(item)
        lib.event("feedback", {"rating": args.rating, "note": args.note}, item["id"])
        return {"id": item["id"], "ratings": item["ratings"]}
    if cmd == "preferences":
        return preferences(lib, args)
    if cmd == "maintain":
        return maintain(lib, args)
    if cmd == "history":
        sql, params = "SELECT seq,at,skill_id,action,data FROM events", []
        if args.skill:
            sql += " WHERE skill_id=?"
            params.append(lib.resolve(args.skill)["id"])
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(args.limit)
        return [{"seq": seq, "at": at, "skill_id": sid, "action": action, "data": json.loads(data)}
                for seq, at, sid, action, data in lib.db.execute(sql, params)]
    if cmd == "export":
        data = {"schema_version": 1, "exported_at": now(), "skills": lib.all(), "preferences": lib.prefs(),
                "trials": [json.loads(r[0]) for r in lib.db.execute("SELECT data FROM trials ORDER BY id")],
                "events": [{"seq": seq, "at": at, "skill_id": sid, "action": action, "data": json.loads(data)}
                           for seq, at, sid, action, data in lib.db.execute("SELECT seq,at,skill_id,action,data FROM events ORDER BY seq")]}
        output = Path(args.output).expanduser().resolve()
        if output == lib.path:
            raise ValueError("Export destination cannot be the library")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(dumps(data) + "\n")
        return {"exported_to": str(output), "skills": len(data["skills"])}
    raise ValueError("Unknown command")


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--library", help="Absolute SQLite library path")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    scan_p = sub.add_parser("scan")
    scan_p.add_argument("--root", action="append", required=True)
    scan_p.add_argument("--platform")
    for name in ("import-catalog", "upsert"):
        sub.add_parser(name).add_argument("file")
    ann = sub.add_parser("annotate")
    ann.add_argument("skill")
    ann.add_argument("file")
    ann.add_argument("--reason", required=True)
    sub.add_parser("show").add_argument("skill")
    ask = sub.add_parser("ask", help="Answer a natural-language question about the personal Skill library")
    ask.add_argument("question")
    ask.add_argument("--skill", help="Exact Skill id or name when asking for details")
    ask.add_argument("--limit", type=positive, default=10)
    listing = sub.add_parser("list")
    listing.add_argument("--state", choices=["active", "candidate", "archived", "all"], default="active")
    listing.add_argument("--category")
    listing.add_argument("--favorites-only", action="store_true")
    listing.add_argument("--installed-only", action="store_true")
    rec = sub.add_parser("recommend")
    rec.add_argument("query")
    rec.add_argument("--category")
    rec.add_argument("--tag", action="append")
    rec.add_argument("--platform")
    rec.add_argument("--strict-platform", action="store_true")
    rec.add_argument("--cost", choices=sorted(ENUMS["cost"]))
    rec.add_argument("--offline", choices=sorted(ENUMS["offline"]))
    rec.add_argument("--difficulty", choices=sorted(ENUMS["difficulty"]))
    rec.add_argument("--language")
    rec.add_argument("--favorites-only", action="store_true")
    rec.add_argument("--installed-only", action="store_true")
    rec.add_argument("--exclude", action="append")
    rec.add_argument("--limit", type=positive, default=3)
    rec.add_argument("--broad", action="store_true", help="Include weak exploratory matches, clearly labeled low evidence")
    recruit_p = sub.add_parser("recruit", help="Add externally discovered skills to the candidate area")
    recruit_p.add_argument("file")
    recruit_p.add_argument("--need", required=True, help="User need or library gap that motivated discovery")
    promote = sub.add_parser("promote", help="Move a trialed candidate into the active library")
    promote.add_argument("skill")
    promote.add_argument("--reason", required=True)
    compare_p = sub.add_parser("compare", help="List potentially overlapping active and candidate skills for review")
    compare_p.add_argument("--threshold", type=float, default=0.25)
    compare_p.add_argument("--limit", type=positive, default=30)
    doctor_p = sub.add_parser("doctor", help="Inspect coverage, overlap, candidates, and maintenance health")
    doctor_p.add_argument("--unused-days", type=positive, default=90)
    doctor_p.add_argument("--review-days", type=positive, default=30)
    doctor_p.add_argument("--candidate-days", type=positive, default=30)
    doctor_p.add_argument("--threshold", type=float, default=0.25)
    doctor_p.add_argument("--crowded-at", type=positive, default=4)
    doctor_p.add_argument("--limit", type=positive, default=20)
    passport_p = sub.add_parser("passport", help="Show one skill's facts and personal evidence")
    passport_p.add_argument("skill")
    passport_p.add_argument("--limit", type=positive, default=10)
    duel_p = sub.add_parser("duel", help="Compare two or more similar skills using recorded evidence")
    duel_p.add_argument("skills", nargs="+")
    explore_p = sub.add_parser("explore", help="Surface one relevant but under-tried active skill")
    explore_p.add_argument("query")
    explore_p.add_argument("--category")
    explore_p.add_argument("--platform")
    explore_p.add_argument("--strict-platform", action="store_true")
    explore_p.add_argument("--installed-only", action="store_true")
    for name in ("favorite", "archive", "restore"):
        change = sub.add_parser(name)
        change.add_argument("skill")
        change.add_argument("--reason", required=True)
        if name == "favorite":
            change.add_argument("--remove", action="store_true")
    start = sub.add_parser("trial-start")
    start.add_argument("skill")
    start.add_argument("--task", required=True)
    start.add_argument("--criterion", required=True)
    start.add_argument("--platform", required=True)
    for name, positional in (("trial-finish", "trial"), ("record-use", "skill")):
        finish = sub.add_parser(name)
        finish.add_argument(positional)
        finish.add_argument("--outcome", choices=["success", "failed", "blocked"], required=True)
        finish.add_argument("--evidence")
        finish.add_argument("--note", required=True)
        finish.add_argument("--rating", type=int, choices=range(1, 6))
        if name == "record-use":
            finish.add_argument("--platform", required=True)
    feedback = sub.add_parser("feedback")
    feedback.add_argument("skill")
    feedback.add_argument("--rating", type=int, choices=range(1, 6), required=True)
    feedback.add_argument("--note", required=True)
    pref = sub.add_parser("preferences").add_subparsers(dest="operation", required=True)
    pref.add_parser("show")
    for name in ("set", "unset"):
        edit = pref.add_parser(name)
        edit.add_argument("key")
        if name == "set":
            edit.add_argument("value", help="JSON-encoded value")
        edit.add_argument("--reason", required=True)
    maint = sub.add_parser("maintain")
    maint.add_argument("--unused-days", type=positive)
    maint.add_argument("--review-days", type=positive)
    maint.add_argument("--candidate-days", type=positive, default=30)
    maint.add_argument("--archive-unused", action="store_true")
    maint.add_argument("--archive-stale-candidates", action="store_true")
    maint.add_argument("--reason")
    history = sub.add_parser("history")
    history.add_argument("--skill")
    history.add_argument("--limit", type=positive, default=20)
    sub.add_parser("export").add_argument("output")
    return p


def run(argv=None):
    args = parser().parse_args(argv)
    lib = Library(args.library or default_library())
    try:
        # Serialize read-modify-write operations across separate agent processes.
        lib.db.execute("BEGIN IMMEDIATE")
        result = dispatch(lib, args)
        lib.db.commit()
        return result
    except Exception:
        lib.db.rollback()
        raise
    finally:
        lib.db.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        print(dumps(run()))
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as error:
        print(dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
