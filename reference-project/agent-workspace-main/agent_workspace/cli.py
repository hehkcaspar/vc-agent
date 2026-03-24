"""CLI entry point for agent_workspace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import llm_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agent-workspace",
        description="Agentic Workspace Processor — ReAct agent for document workspaces.",
    )
    sub = parser.add_subparsers(dest="command")

    # -- init --------------------------------------------------------------
    init_p = sub.add_parser("init", help="Initialize a new workspace")
    init_p.add_argument(
        "--dir", "-d",
        type=str,
        default=".",
        help="Directory to initialize (default: current directory)",
    )

    # -- run ---------------------------------------------------------------
    run_p = sub.add_parser("run", help="Run agent on a workspace with a task")
    run_p.add_argument(
        "--workspace", "-w",
        type=str,
        default=".",
        help="Path to workspace root (default: current directory)",
    )
    task_group = run_p.add_mutually_exclusive_group(required=True)
    task_group.add_argument("--task", "-t", type=str, help="Task string")
    task_group.add_argument("--task-file", type=str, help="Path to task file (.md or .txt)")
    run_p.add_argument(
        "--template",
        type=str,
        help="Name of template in workspace templates/ dir",
    )
    run_p.add_argument(
        "--var",
        action="append",
        default=[],
        help="Template variable in KEY=VALUE format (repeatable)",
    )
    run_p.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output (only show final result)",
    )

    # -- scan --------------------------------------------------------------
    scan_p = sub.add_parser("scan", help="Scan and show workspace resources")
    scan_p.add_argument("--workspace", "-w", type=str, default=".")

    # -- diff --------------------------------------------------------------
    diff_p = sub.add_parser("diff", help="Show changes since last run")
    diff_p.add_argument("--workspace", "-w", type=str, default=".")

    # -- artifacts ---------------------------------------------------------
    artifacts_p = sub.add_parser("artifacts", help="List all artifacts")
    artifacts_p.add_argument("--workspace", "-w", type=str, default=".")

    # -- memory ------------------------------------------------------------
    memory_p = sub.add_parser("memory", help="Show memory contents")
    memory_p.add_argument("--workspace", "-w", type=str, default=".")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "init":
        _cmd_init(args)
    elif args.command == "scan":
        _cmd_scan(args)
    elif args.command == "run":
        _cmd_run(args)
    elif args.command == "diff":
        _cmd_diff(args)
    elif args.command == "artifacts":
        _cmd_artifacts(args)
    elif args.command == "memory":
        _cmd_memory(args)


DEFAULT_CONFIG_YAML = '''\
# Workspace Configuration
workspace:
  resources_dir: resources
  instructions_dir: instructions
  artifacts_dir: artifacts
  snapshots_dir: .snapshots

extraction:
  max_text_chars: 15000
  max_images: 10
  max_excel_rows: 100
  max_excel_sheets: 5

agent:
  max_iterations: 20
  memory_turns: 20
  trace_enabled: true
'''


def _cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new workspace with folder structure."""
    workspace_root = Path(args.dir).resolve()
    
    # Create directories
    dirs = [
        workspace_root / "resources",
        workspace_root / "instructions" / "templates",
        workspace_root / "artifacts" / "reports",
        workspace_root / "artifacts" / "memory",
        workspace_root / "artifacts" / "skills",
        workspace_root / "artifacts" / "traces",
        workspace_root / "artifacts" / "settings",
        workspace_root / ".snapshots",
    ]
    
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    
    # Create default config.yaml
    config_path = workspace_root / "config.yaml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
        try:
            print(f"Created: {config_path.relative_to(Path.cwd())}")
        except ValueError:
            print(f"Created: {config_path}")
    
    # Create .gitignore for snapshots
    gitignore_path = workspace_root / ".snapshots" / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text("*\n", encoding="utf-8")
    
    print(f"\nInitialized workspace at: {workspace_root}")
    print("\nDirectory structure:")
    print("  resources/        -> Place your input files here")
    print("  instructions/     -> Task definitions and templates")
    print("  artifacts/        -> Agent-generated outputs")
    print("  .snapshots/       -> File change tracking")


def _cmd_scan(args: argparse.Namespace) -> None:
    from .config import load_workspace_config
    from .workspace import Workspace

    workspace_root = Path(args.workspace).resolve()
    cfg = load_workspace_config(workspace_root)
    ws = Workspace(workspace_root, cfg.resources_dir, cfg.snapshots_dir)

    manifest = ws.scan()
    print(f"Workspace: {workspace_root}")
    print(f"Resources: {len(manifest)} files\n")
    for path, entry in sorted(manifest.items()):
        size_kb = entry["size"] / 1024
        print(f"  [{entry['file_type']:>6}] {path}  ({size_kb:.1f} KB)")


def _cmd_diff(args: argparse.Namespace) -> None:
    """Show changes since last run."""
    from .config import load_workspace_config
    from .workspace import Workspace

    workspace_root = Path(args.workspace).resolve()
    cfg = load_workspace_config(workspace_root)
    ws = Workspace(workspace_root, cfg.resources_dir, cfg.snapshots_dir)

    previous = ws.load_snapshot()
    current = ws.scan()

    if previous is None:
        print("No previous snapshot found. This appears to be the first run.")
        print(f"Current resources: {len(current)} files")
        return

    diff = ws.diff(current, previous)
    print(f"Workspace: {workspace_root}")
    print(f"Changes since last run:\n")
    
    if diff["added"]:
        print(f"Added ({len(diff['added'])}):")
        for f in diff["added"]:
            print(f"  + {f}")
        print()
    
    if diff["modified"]:
        print(f"Modified ({len(diff['modified'])}):")
        for f in diff["modified"]:
            print(f"  ~ {f}")
        print()
    
    if diff["removed"]:
        print(f"Removed ({len(diff['removed'])}):")
        for f in diff["removed"]:
            print(f"  - {f}")
        print()
    
    if diff["unchanged"]:
        print(f"Unchanged ({len(diff['unchanged'])} files)")
    
    if not any([diff["added"], diff["modified"], diff["removed"]]):
        print("No changes detected.")


def _cmd_artifacts(args: argparse.Namespace) -> None:
    """List all artifacts."""
    from .config import load_workspace_config

    workspace_root = Path(args.workspace).resolve()
    cfg = load_workspace_config(workspace_root)
    artifacts_dir = workspace_root / cfg.artifacts_dir

    if not artifacts_dir.exists():
        print(f"No artifacts directory found at: {artifacts_dir}")
        return

    print(f"Artifacts in: {artifacts_dir}\n")
    
    for subdir in sorted(artifacts_dir.iterdir()):
        if not subdir.is_dir():
            continue
        files = list(subdir.rglob("*"))
        files = [f for f in files if f.is_file()]
        if files:
            print(f"{subdir.name}/ ({len(files)} files)")
            for f in sorted(files):
                rel = f.relative_to(artifacts_dir)
                size_kb = f.stat().st_size / 1024
                print(f"  {rel} ({size_kb:.1f} KB)")
            print()


def _cmd_memory(args: argparse.Namespace) -> None:
    """Show memory contents."""
    from .config import load_workspace_config

    workspace_root = Path(args.workspace).resolve()
    cfg = load_workspace_config(workspace_root)
    memory_dir = workspace_root / cfg.artifacts_dir / "memory"

    if not memory_dir.exists():
        print(f"No memory directory found at: {memory_dir}")
        return

    files = sorted(memory_dir.glob("*.md"))
    if not files:
        print(f"No memory files found in: {memory_dir}")
        return

    print(f"Memory files in: {memory_dir}\n")
    for f in files:
        content = f.read_text(encoding="utf-8")
        print(f"=== {f.name} ===")
        print(content[:500] if len(content) > 500 else content)
        if len(content) > 500:
            print(f"... ({len(content) - 500} more chars)")
        print()


def _cmd_run(args: argparse.Namespace) -> None:
    from .agent import run_agent
    from .prompts import resolve_template

    workspace_root = Path(args.workspace).resolve()

    # Resolve task content
    if args.task_file:
        task_path = Path(args.task_file)
        if not task_path.exists():
            print(f"[error] Task file not found: {args.task_file}", file=sys.stderr)
            sys.exit(1)
        task = task_path.read_text(encoding="utf-8")
    elif args.template:
        templates_dir = workspace_root / "templates"
        variables = {}
        for v in args.var:
            if "=" not in v:
                print(f"[error] --var must be KEY=VALUE, got: {v}", file=sys.stderr)
                sys.exit(1)
            key, value = v.split("=", 1)
            variables[key] = value
        try:
            task = resolve_template(templates_dir, args.template, variables)
        except FileNotFoundError as e:
            print(f"[error] {e}", file=sys.stderr)
            sys.exit(1)
    else:
        task = args.task

    # Validate LLM config early
    try:
        llm_settings.validate()
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        print("Set LLM_API_KEY environment variable or add it to .env", file=sys.stderr)
        sys.exit(1)

    # Run agent with error handling
    try:
        run_agent(workspace_root, task, verbose=not args.quiet)
    except KeyboardInterrupt:
        print("\n[agent] Interrupted by user", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n[error] Agent failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
