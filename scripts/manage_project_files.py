#!/usr/bin/env python3
"""Preview-first project file-tree governance for team workspaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = SKILL_ROOT / "references" / "default-policy.json"
ASSETS = SKILL_ROOT / "assets"
PROJECT_RE = re.compile(r"^\d{6}\s+\S.*$")
MISNAMED_DIRS = {
    "01_规则": "01_需求输入",
    "02_项目策划": "02-项目策划",
    "tmp": "temp",
}
ALLOWED_CONFIG_KEYS = {
    "intake_days",
    "inactive_days",
    "infrastructure_dirs",
    "ai_mappings",
    "project_exceptions",
}


class GovernanceError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"无法读取JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise GovernanceError(f"JSON顶层必须是对象：{path}")
    return value


def load_policy(config_path: str | None = None) -> dict[str, Any]:
    policy = read_json(DEFAULT_POLICY)
    if not config_path:
        return policy
    custom = read_json(Path(config_path).resolve())
    unknown = set(custom) - ALLOWED_CONFIG_KEYS
    if unknown:
        raise GovernanceError(f"配置包含不允许的字段：{', '.join(sorted(unknown))}")
    if "intake_days" in custom:
        days = int(custom["intake_days"])
        if days < 1 or days > int(policy["intake_days"]):
            raise GovernanceError("intake_days只能在1到默认时限之间，不能放宽治理底线")
        policy["intake_days"] = days
    if "inactive_days" in custom:
        policy["inactive_days"] = max(1, int(custom["inactive_days"]))
    if "infrastructure_dirs" in custom:
        policy["infrastructure_dirs"] = sorted(
            set(policy["infrastructure_dirs"]) | set(custom["infrastructure_dirs"])
        )
    if "ai_mappings" in custom:
        policy["ai_mappings"].update(custom["ai_mappings"])
    if "project_exceptions" in custom:
        if not isinstance(custom["project_exceptions"], dict):
            raise GovernanceError("project_exceptions必须是对象")
        policy["project_exceptions"] = custom["project_exceptions"]
    return policy


def project_policy(policy: dict[str, Any], name: str) -> dict[str, Any]:
    merged = dict(policy)
    merged["infrastructure_dirs"] = list(policy["infrastructure_dirs"])
    exception = policy.get("project_exceptions", {}).get(name, {})
    if not isinstance(exception, dict):
        raise GovernanceError(f"项目例外必须是对象：{name}")
    allowed = {"intake_days", "infrastructure_dirs"}
    unknown = set(exception) - allowed
    if unknown:
        raise GovernanceError(f"项目 {name} 包含不允许的例外字段：{', '.join(sorted(unknown))}")
    if "intake_days" in exception:
        days = int(exception["intake_days"])
        if days < 1 or days > int(policy["intake_days"]):
            raise GovernanceError(f"项目 {name} 的 intake_days 只能缩短，不能放宽")
        merged["intake_days"] = days
    if "infrastructure_dirs" in exception:
        merged["infrastructure_dirs"] = sorted(
            set(policy["infrastructure_dirs"]) | set(exception["infrastructure_dirs"])
        )
    return merged


def render_asset(name: str, values: dict[str, Any]) -> str:
    text = (ASSETS / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    unresolved = re.findall(r"\{\{[A-Z0-9_]+\}\}", text)
    if unresolved:
        raise GovernanceError(f"模板变量未替换：{', '.join(sorted(set(unresolved)))}")
    return text


def within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def require_root(path: str) -> Path:
    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        raise GovernanceError(f"工作区不存在或不是目录：{root}")
    return root


def projects(root: Path, policy: dict[str, Any]) -> list[Path]:
    archive = policy["archive_dir"]
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and p.name != archive and not p.name.startswith(".") and not p.name.startswith("00_")
    )


def markdown_ai_table(mappings: dict[str, str]) -> str:
    rows = ["| 智能体 | 后缀 |", "|---|---|"]
    rows.extend(f"| {name} | `{abbr}` |" for name, abbr in mappings.items())
    return "\n".join(rows)


def common_values(root: Path, policy: dict[str, Any], knowledge_root: str | None = None) -> dict[str, Any]:
    return {
        "POLICY_VERSION": policy["policy_version"],
        "DATE": datetime.now().strftime("%Y-%m-%d"),
        "WORKSPACE_ROOT": str(root),
        "KNOWLEDGE_ROOT": str(Path(knowledge_root).resolve()) if knowledge_root else "未配置",
        "INTAKE_DAYS": policy["intake_days"],
        "INACTIVE_DAYS": policy["inactive_days"],
        "AI_MAPPING_TABLE": markdown_ai_table(policy["ai_mappings"]),
    }


def show_preview(title: str, entries: list[tuple[str, Path]]) -> None:
    print(f"【预览】{title}")
    for kind, path in entries:
        print(f"- {kind}: {path}")
    print("未执行任何写入。确认后使用 --apply。")


def write_absent(path: Path, content: str) -> str:
    if path.exists():
        return "保留已有"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return "已创建"


def init_workspace(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    policy = load_policy(args.config)
    values = common_values(root, policy, args.knowledge_root)
    files = {
        root / "00_工作区文件管理规则.md": render_asset("workspace-rules.md", values),
        root / "00_巡检清单.md": render_asset("audit-checklist.md", values),
        root / "00_项目总索引.md": render_asset("project-index.md", values),
    }
    dirs = [root, root / policy["archive_dir"]]
    knowledge_dirs: list[Path] = []
    knowledge_files: dict[Path, str] = {}
    if args.knowledge_root:
        kroot = Path(args.knowledge_root).resolve()
        knowledge_dirs = [kroot / name for name in ("方法论", "SOP", "模板", "案例与复盘")]
        knowledge_files[kroot / "README.md"] = render_asset("knowledge-readme.md", values)
    preview = [("目录", p) for p in dirs + knowledge_dirs] + [("文件", p) for p in files | knowledge_files]
    if not args.apply:
        show_preview("建立团队工作区", preview)
        return 0
    for path in dirs + knowledge_dirs:
        path.mkdir(parents=True, exist_ok=True)
        print(f"目录就绪: {path}")
    for path, content in (files | knowledge_files).items():
        print(f"{write_absent(path, content)}: {path}")
    return 0


def init_project(args: argparse.Namespace) -> int:
    root = require_root(args.root)
    policy = load_policy(args.config)
    if not PROJECT_RE.match(args.name):
        raise GovernanceError("项目名必须符合：YYYYMM 项目名")
    index = root / "00_项目总索引.md"
    if not index.exists():
        raise GovernanceError("工作区尚未初始化：缺少 00_项目总索引.md")
    project = root / args.name
    pp = project_policy(policy, args.name)
    dirs = [project / name for name in pp["required_dirs"]]
    if args.with_unfiled:
        dirs.append(project / "99_未归档")
    title = re.sub(r"^\d{6}\s+", "", args.name)
    values = common_values(root, pp)
    values.update({"PROJECT_NAME": args.name, "PROJECT_TITLE": title})
    files = {
        project / "00_README.md": render_asset("project-readme.md", values),
        project / "00_文件分类与归档规则.md": render_asset("project-classification.md", values),
    }
    preview = [("目录", p) for p in dirs] + [("文件", p) for p in files]
    preview.append(("更新索引", index))
    if not args.apply:
        show_preview(f"建立项目 {args.name}", preview)
        return 0
    project.mkdir(parents=True, exist_ok=True)
    for path in dirs:
        path.mkdir(parents=True, exist_ok=True)
        print(f"目录就绪: {path}")
    for path, content in files.items():
        print(f"{write_absent(path, content)}: {path}")
    text = index.read_text(encoding="utf-8")
    if f"| {args.name} |" not in text:
        line = f"| {args.name} | 进行中 | {datetime.now():%Y-%m-%d} | 待补充 |\n"
        with index.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
        print(f"已更新索引: {index}")
    else:
        print(f"索引已有项目，保持不变: {index}")
    return 0


CLASSIFICATIONS = {
    "unknown": ("00_输入待分配", "信息不足，先进入临时分流入口", "不得长期停留", "核实后进入01、03、04、05或99"),
    "requirement": ("01_需求输入", "对交付形成约束的需求、确认口径或事实输入", "不放内部草稿和外部参考", "形成决策进入02，执行进入04"),
    "planning": ("02-项目策划", "项目内稳定的Brief、策略、计划、规则或SOP", "不放未验证草稿和原始附件", "跨项目验证后进入05"),
    "reference": ("03_参考资料", "外部原件、报告、案例或研究资料", "不直接改写原件", "摘要或应用成果进入02或04"),
    "execution": ("04_项目执行资料", "当前生产、审核、交付和实施记录", "不放长期规则或纯原始资料", "定稿升级02/05，旧稿进入98"),
    "knowledge": ("05_知识沉淀", "已验证且可跨项目复用的经验", "不放客户机密和一次性细节", "成熟后提升至团队知识库"),
    "obsolete": ("98_历史归档", "已替代、重复或失效", "不放当前有效版本", "只追溯"),
    "misc": ("99_未归档", "非业务杂物或暂不治理内容", "不放唯一业务原件", "定期复核或清理"),
    "temporary": ("temp", "可再生成的缓存、锁文件或中间产物", "不放唯一业务原件和正式交付物", "可定期清理"),
}


def classify(args: argparse.Namespace) -> int:
    folder, reason, prohibited, flow = CLASSIFICATIONS[args.kind]
    result = {"file": args.name, "kind": args.kind, "destination": folder, "reason": reason, "prohibited": prohibited, "next_flow": flow}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"建议位置：`{folder}`\n理由：{reason}\n不应包含：{prohibited}\n下一步：{flow}")
    return 0


def issue(code: str, severity: str, path: Path, message: str, project: str | None = None, **extra: Any) -> dict[str, Any]:
    value = {"code": code, "severity": severity, "path": str(path), "message": message}
    if project:
        value["project"] = project
    value.update(extra)
    return value


def age_days(path: Path) -> int:
    return max(0, int((datetime.now().timestamp() - path.stat().st_mtime) // 86400))


def latest_age(path: Path) -> int:
    latest = path.stat().st_mtime
    for candidate in path.rglob("*"):
        try:
            latest = max(latest, candidate.stat().st_mtime)
        except OSError:
            pass
    return max(0, int((datetime.now().timestamp() - latest) // 86400))


def audit_issues(root: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for filename in policy["workspace_files"]:
        path = root / filename
        if not path.is_file():
            found.append(issue("workspace_file_missing", "error", path, f"缺少工作区入口文件：{filename}"))
    archive = root / policy["archive_dir"]
    if not archive.is_dir():
        found.append(issue("archive_dir_missing", "error", archive, "缺少工作区归档目录"))
    for project in projects(root, policy):
        name = project.name
        pp = project_policy(policy, name)
        if not PROJECT_RE.match(name):
            found.append(issue("project_name_invalid", "warning", project, "项目名不符合 YYYYMM 项目名", name))
        entries = {p.name: p for p in project.iterdir()}
        for dirname in pp["required_dirs"]:
            path = project / dirname
            if not path.is_dir():
                found.append(issue("required_dir_missing", "error", path, f"缺少标准目录：{dirname}", name, target=str(path)))
        for wrong, correct in MISNAMED_DIRS.items():
            if (project / wrong).is_dir() and not (project / correct).exists():
                found.append(issue("directory_misnamed", "error", project / wrong, f"目录误命名：{wrong} → {correct}", name, target=str(project / correct)))
        for filename in pp["project_files"]:
            path = project / filename
            if not path.is_file():
                found.append(issue("project_file_missing", "error", path, f"缺少项目入口文件：{filename}", name))
        intake = project / "00_输入待分配"
        if intake.is_dir():
            for path in intake.rglob("*"):
                if path.is_file() and age_days(path) > int(pp["intake_days"]):
                    found.append(issue("intake_stale", "warning", path, f"输入待分配已停留{age_days(path)}天，超过{pp['intake_days']}天", name))
        allowed_dirs = set(pp["required_dirs"]) | set(pp["optional_dirs"]) | set(pp["infrastructure_dirs"])
        allowed_dirs |= set(MISNAMED_DIRS)
        for path in entries.values():
            if path.is_dir() and path.name not in allowed_dirs:
                found.append(issue("extra_root_dir", "warning", path, "项目根目录存在未获准的非标准目录", name))
            elif path.is_file():
                allowed_entry = path.name.startswith("00_") and path.suffix.lower() == ".md"
                if not allowed_entry and path.suffix.lower() in pp["business_extensions"]:
                    found.append(issue("root_business_file", "error", path, "业务文件散放在项目根目录，需要人工分类", name))
        history = project / "98_历史归档"
        if history.is_dir():
            for path in history.rglob("*"):
                relative_parts = path.relative_to(history).parts
                marked_in_path = any(
                    marker in part
                    for part in relative_parts
                    for marker in pp["archive_markers"]
                )
                if path.is_file() and not path.name.startswith("00_") and not marked_in_path:
                    found.append(issue("archive_marker_missing", "warning", path, "历史归档文件未标记旧版、已替代或重复", name))
        for path in project.rglob("*"):
            if not path.is_file():
                continue
            if any(term in path.name for term in pp["ambiguous_status_terms"]):
                found.append(issue("ambiguous_status", "warning", path, "文件名使用含义不清的版本状态", name))
            lower = path.name.lower()
            for ai_name, abbr in pp["ai_mappings"].items():
                full = f"_{ai_name.lower()}_"
                if full in lower:
                    found.append(issue("ai_suffix_full_name", "warning", path, f"AI后缀应使用缩写：{full} → _{abbr}_", name, old=full, new=f"_{abbr}_"))
                    break
        readme = project / "00_README.md"
        if readme.is_file():
            try:
                content = readme.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                found.append(issue("readme_encoding", "error", readme, "README不是有效UTF-8", name))
            else:
                missing_maps = [f"{key}={value}" for key, value in pp["ai_mappings"].items() if key not in content or f"`{value}`" not in content]
                if missing_maps:
                    found.append(issue("readme_ai_map_mismatch", "warning", readme, f"README缺少或未同步AI映射：{', '.join(missing_maps)}", name))
        execution = project / "04_项目执行资料"
        if execution.is_dir():
            for path in execution.rglob("*"):
                if path.is_file() and "已定稿" in path.name:
                    found.append(issue("finalized_review", "info", path, "已定稿执行文件需判断是否升级到02或05", name))
        inactive = latest_age(project)
        if inactive > int(pp["inactive_days"]):
            found.append(issue("project_inactive", "info", project, f"项目已{inactive}天无活动，建议评估结案归档", name))
    return found


def print_audit(root: Path, found: list[dict[str, Any]], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps({"workspace": str(root), "issue_count": len(found), "issues": found}, ensure_ascii=False, indent=2))
        return
    print(f"# 工作区巡检：{root}")
    print(f"\n发现 {len(found)} 项。" if found else "\n全部检查通过。")
    for item in found:
        project = f"[{item['project']}] " if item.get("project") else ""
        print(f"- **{item['severity'].upper()} · {item['code']}** {project}{item['message']} — `{item['path']}`")
    print("\n仅只读检查，未修改、移动或删除任何文件。")


def audit_command(args: argparse.Namespace, verify_mode: bool = False) -> int:
    root = require_root(args.root)
    found = audit_issues(root, load_policy(args.config))
    print_audit(root, found, args.format)
    return 1 if verify_mode and found else 0


def fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = path.stat()
        return {"exists": True, "type": "file", "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}
    rows = []
    for child in sorted(path.rglob("*")):
        try:
            stat = child.stat()
            rows.append((str(child.relative_to(path)), child.is_dir(), stat.st_size, stat.st_mtime_ns))
        except OSError:
            rows.append((str(child.relative_to(path)), "unreadable"))
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"exists": True, "type": "dir", "sha256": hashlib.sha256(encoded).hexdigest()}


def add_action(actions: list[dict[str, Any]], action_type: str, reason: str, source: Path | None = None, target: Path | None = None, executable: bool = True) -> None:
    value: dict[str, Any] = {"type": action_type, "reason": reason, "executable": executable}
    if source:
        value["source"] = str(source)
        value["source_fingerprint"] = fingerprint(source)
    if target:
        value["target"] = str(target)
    actions.append(value)


def plan_fix(args: argparse.Namespace) -> int:
    root = require_root(args.root)
    policy = load_policy(args.config)
    found = audit_issues(root, policy)
    actions: list[dict[str, Any]] = []
    for item in found:
        code = item["code"]
        path = Path(item["path"])
        if code in {"archive_dir_missing", "required_dir_missing"}:
            add_action(actions, "mkdir", item["message"], target=path)
        elif code == "directory_misnamed":
            add_action(actions, "move", item["message"], source=path, target=Path(item["target"]))
        elif code == "archive_marker_missing":
            target = path.with_name(f"{path.stem}_旧版{path.suffix}")
            add_action(actions, "rename", item["message"], source=path, target=target)
        elif code == "ai_suffix_full_name":
            target = path.with_name(re.sub(re.escape(item["old"]), item["new"], path.name, flags=re.IGNORECASE))
            add_action(actions, "rename", item["message"], source=path, target=target)
        else:
            add_action(actions, "review", item["message"], source=path if path.exists() else None, executable=False)
    if args.mapping:
        mapping = read_json(Path(args.mapping).resolve())
        allowed_dest = set(policy["required_dirs"]) | set(policy["optional_dirs"])
        for relative, destination in mapping.items():
            source = (root / relative).resolve()
            if not within(root, source) or not source.is_file():
                raise GovernanceError(f"映射源文件无效或越界：{relative}")
            project = source.relative_to(root).parts[0]
            if destination not in allowed_dest:
                raise GovernanceError(f"映射目标不是标准目录：{destination}")
            target = root / project / destination / source.name
            add_action(actions, "move", f"用户确认分类到 {destination}", source=source, target=target)
    for index, action in enumerate(actions, 1):
        action["id"] = f"A{index:03d}"
    plan = {
        "schema_version": 1,
        "policy_version": policy["policy_version"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(root),
        "actions": actions,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"整改计划已生成：{output}")
    print(f"共 {len(actions)} 项，其中 {sum(1 for a in actions if a['executable'])} 项可在确认后执行。")
    print("尚未移动、重命名或删除任何业务文件。")
    return 0


def apply_plan(args: argparse.Namespace) -> int:
    if args.confirm != "APPLY":
        raise GovernanceError("执行整改必须提供 --confirm APPLY")
    plan = read_json(Path(args.plan).resolve())
    root = require_root(plan.get("workspace", ""))
    approved = {item.strip() for item in args.approve.split(",") if item.strip()}
    if not approved:
        raise GovernanceError("必须通过 --approve 指定至少一个操作ID")
    actions = {item.get("id"): item for item in plan.get("actions", [])}
    missing = approved - set(actions)
    if missing:
        raise GovernanceError(f"计划中不存在操作ID：{', '.join(sorted(missing))}")
    selected = [actions[item_id] for item_id in sorted(approved)]
    preflight: list[tuple[dict[str, Any], Path | None, Path | None]] = []
    for action in selected:
        if not action.get("executable") or action.get("type") == "review":
            raise GovernanceError(f"操作 {action['id']} 仅供复核，不能执行")
        source = Path(action["source"]).resolve() if action.get("source") else None
        target = Path(action["target"]).resolve() if action.get("target") else None
        for path in (source, target):
            if path and not within(root, path):
                raise GovernanceError(f"操作 {action['id']} 越出工作区：{path}")
        if source and fingerprint(source) != action.get("source_fingerprint"):
            raise GovernanceError(f"操作 {action['id']} 的源文件在计划后发生变化，已停止全部执行")
        if action["type"] in {"move", "rename"} and target and target.exists():
            raise GovernanceError(f"操作 {action['id']} 的目标已存在，已停止全部执行：{target}")
        preflight.append((action, source, target))
    for action, source, target in preflight:
        if action["type"] == "mkdir":
            assert target is not None
            target.mkdir(parents=True, exist_ok=True)
        elif action["type"] in {"move", "rename"}:
            assert source is not None and target is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        else:
            raise GovernanceError(f"不支持的操作类型：{action['type']}")
        print(f"已执行 {action['id']} {action['type']}: {target}")
    print("未永久删除任何文件。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="团队项目文件树治理")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-workspace", help="预览或建立工作区")
    p.add_argument("--root", required=True)
    p.add_argument("--knowledge-root")
    p.add_argument("--config")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=init_workspace)

    p = sub.add_parser("init-project", help="预览或建立项目")
    p.add_argument("--root", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--config")
    p.add_argument("--with-unfiled", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=init_project)

    p = sub.add_parser("classify", help="解释文件归属")
    p.add_argument("--name", required=True)
    p.add_argument("--kind", choices=sorted(CLASSIFICATIONS), default="unknown")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.set_defaults(func=classify)

    for command, help_text, verify_mode in (("audit", "只读巡检", False), ("verify", "整改后复检", True)):
        p = sub.add_parser(command, help=help_text)
        p.add_argument("--root", required=True)
        p.add_argument("--config")
        p.add_argument("--format", choices=("markdown", "json"), default="markdown")
        p.set_defaults(func=lambda a, mode=verify_mode: audit_command(a, mode))

    p = sub.add_parser("plan-fix", help="生成整改计划")
    p.add_argument("--root", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--mapping")
    p.add_argument("--config")
    p.set_defaults(func=plan_fix)

    p = sub.add_parser("apply", help="执行已确认的整改计划")
    p.add_argument("--plan", required=True)
    p.add_argument("--approve", required=True)
    p.add_argument("--confirm", required=True)
    p.set_defaults(func=apply_plan)
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except GovernanceError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
