# UI layout exports (engineering mode)

Schema: **`sa-ui-layout/v1`** · `mode: draft-export`  
Workspace: §B.23 · Skill: `workspace-ui-design` §2b · Product: `auto-larchui`

## Storage path (not system cache)

**Default:**

```text
…\AI projects\smartLarch Platform\ui\
```

Change: **Settings → 工程模式导出目录** (QSettings `eng_export_dir`).  
The eng toolbar always shows the full path; export dialog and log print it too.

| File | Role | Keep? |
|------|------|--------|
| `layout.current.json` | Latest export | Yes |
| `layout.export-YYYYMMDD_HHMMSS.json` | Timestamped draft | **Current + one previous only** |
| `README.md` | This file | Yes |

## Workflow (draft only — live UI does not change)

1. GUI → log bar **🔧** (or `Launch-smartLarch-Platform.ps1 --eng`).
2. **左栏 / 右栏 / 日志高** = main window Groups | plot | params + bottom log targets.
3. **选控件** → left-click a control → **right-click**: set width / height / rename (zh+en).
4. **备注** for intent numbers cannot express.
5. **导出** → JSON under the path on the toolbar.
6. Give the file to an agent: “按 JSON 改正式版源码”.

## Agent apply

1. Read **`user_notes`** first.  
2. Apply `panels` / `splitters` / `widgets` / `text` to **source** (`main_window.py`, `i18n.py`).  
3. Bump `APP_VERSION`, update CHANGELOG, quit GUI.  
4. Do not expect the running app to already match draft sizes.
