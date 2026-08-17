# 外部配置

将团队路径、AI名单和项目例外保存在 Skill 外部的 JSON 文件中，并通过 `--config` 传入。未提供配置时使用 `default-policy.json`。

```json
{
  "intake_days": 5,
  "infrastructure_dirs": [".git", ".codex", ".agents", ".vscode", "scripts"],
  "ai_mappings": {"Codex": "cdx", "WorkBuddy": "wbdy"},
  "project_exceptions": {
    "202609 示例项目": {
      "intake_days": 2,
      "infrastructure_dirs": ["render-cache"]
    }
  }
}
```

标量覆盖默认值；列表与默认白名单取并集；项目例外只应用于同名项目。外部配置不能移除必需目录，也不能允许删除或免除确认。
