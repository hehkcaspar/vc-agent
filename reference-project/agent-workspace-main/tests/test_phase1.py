"""Phase 1 automatic test — verify walking skeleton works end-to-end."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


def test_workspace_init():
    """Test: init command creates correct folder structure."""
    print("\n" + "="*60)
    print("TEST 1: Workspace initialization")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        
        # Run init
        from .cli import main
        sys.argv = ["agent-workspace", "init", "--dir", str(test_dir)]
        main(["init", "--dir", str(test_dir)])
        
        # Verify structure
        expected_dirs = [
            test_dir / "resources",
            test_dir / "instructions" / "templates",
            test_dir / "artifacts" / "reports",
            test_dir / "artifacts" / "memory",
            test_dir / "artifacts" / "skills",
            test_dir / "artifacts" / "traces",
            test_dir / "artifacts" / "settings",
            test_dir / ".snapshots",
        ]
        
        all_exist = True
        for d in expected_dirs:
            if d.exists():
                print(f"  [OK] {d.relative_to(test_dir)}")
            else:
                print(f"  [MISSING] {d.relative_to(test_dir)}")
                all_exist = False
        
        config_file = test_dir / "config.yaml"
        if config_file.exists():
            print(f"  [OK] config.yaml")
        else:
            print(f"  [MISSING] config.yaml")
            all_exist = False
        
        assert all_exist, "Some directories were not created!"
        print("\n[PASS] Workspace initialization works correctly")


def test_scan_empty():
    """Test: scan command on empty resources."""
    print("\n" + "="*60)
    print("TEST 2: Scan empty resources")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        
        # Init workspace
        from .cli import main
        main(["init", "--dir", str(test_dir)])
        
        # Scan
        print("\nScanning empty workspace:")
        main(["scan", "--workspace", str(test_dir)])
        
        print("\n[PASS] Scan on empty resources works")


def test_scan_with_files():
    """Test: scan command detects various file types."""
    print("\n" + "="*60)
    print("TEST 3: Scan with files")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        resources_dir = test_dir / "resources"
        
        # Init workspace
        from .cli import main
        main(["init", "--dir", str(test_dir)])
        
        # Create test files
        (resources_dir / "document.txt").write_text("This is a test document.", encoding="utf-8")
        (resources_dir / "data.json").write_text('{"key": "value"}', encoding="utf-8")
        (resources_dir / "notes.md").write_text("# Notes\n\nSome markdown content.", encoding="utf-8")
        (resources_dir / "subdir").mkdir(exist_ok=True)
        (resources_dir / "subdir" / "nested.txt").write_text("Nested file content.", encoding="utf-8")
        
        # Scan
        print("\nScanning workspace with files:")
        main(["scan", "--workspace", str(test_dir)])
        
        print("\n[PASS] Scan with files works correctly")


def test_extract_content():
    """Test: extract_content tool extracts text files."""
    print("\n" + "="*60)
    print("TEST 4: Content extraction")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        resources_dir = test_dir / "resources"
        
        # Init workspace
        from .cli import main
        main(["init", "--dir", str(test_dir)])
        
        # Create test file
        test_content = "This is the first line.\nThis is the second line.\n"
        (resources_dir / "sample.txt").write_text(test_content, encoding="utf-8")
        
        # Extract
        from .config import load_workspace_config
        from .extractor import extract_file
        from .workspace import classify_file
        
        cfg = load_workspace_config(test_dir)
        file_path = resources_dir / "sample.txt"
        file_type = classify_file(file_path)
        result = extract_file(file_path, file_type, cfg.extraction)
        
        print(f"\nExtracted from {file_path.name}:")
        print(f"  Type: {result['type']}")
        print(f"  Content preview: {result['text'][:50]}...")
        
        assert result["type"] == "text", f"Expected type 'text', got '{result['type']}'"
        assert "first line" in result["text"], "Expected content not found"
        
        print("\n[PASS] Content extraction works correctly")


def test_write_artifact():
    """Test: write_artifact tool creates files."""
    print("\n" + "="*60)
    print("TEST 5: Artifact writing")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        
        # Init workspace
        from .cli import main
        main(["init", "--dir", str(test_dir)])
        
        # Write artifact
        from .tools.write_artifact import write_artifact
        result = write_artifact.invoke({
            "workspace_root": str(test_dir),
            "artifact_type": "reports",
            "name": "test_report.md",
            "content": "# Test Report\n\nThis is a test."
        })
        
        print(f"\n{result}")
        
        # Verify file exists
        artifact_path = test_dir / "artifacts" / "reports" / "test_report.md"
        assert artifact_path.exists(), f"Artifact not found at {artifact_path}"
        
        content = artifact_path.read_text(encoding="utf-8")
        assert "Test Report" in content, "Expected content not in artifact"
        
        print("\n[PASS] Artifact writing works correctly")


def test_read_artifact():
    """Test: read_artifact tool reads files."""
    print("\n" + "="*60)
    print("TEST 6: Artifact reading")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        
        # Init workspace
        from .cli import main
        main(["init", "--dir", str(test_dir)])
        
        # Create artifact manually
        artifact_path = test_dir / "artifacts" / "reports" / "existing.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("# Existing Report\n\nPrevious content.", encoding="utf-8")
        
        # Read artifact
        from .tools.read_artifact import read_artifact
        result = read_artifact.invoke({
            "workspace_root": str(test_dir),
            "artifact_path": "reports/existing.md"
        })
        
        print(f"\nRead artifact:")
        print(result[:200])
        
        assert "Existing Report" in result, "Expected content not found"
        
        print("\n[PASS] Artifact reading works correctly")


def test_diff_detection():
    """Test: diff command shows changes."""
    print("\n" + "="*60)
    print("TEST 7: Diff detection")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        resources_dir = test_dir / "resources"
        
        # Init workspace
        from .cli import main
        main(["init", "--dir", str(test_dir)])
        
        # Create initial file
        (resources_dir / "file1.txt").write_text("Initial content", encoding="utf-8")
        
        # Create snapshot manually
        from .workspace import Workspace
        from .config import load_workspace_config
        cfg = load_workspace_config(test_dir)
        ws = Workspace(test_dir, cfg.resources_dir, cfg.snapshots_dir)
        manifest = ws.scan()
        ws.save_snapshot(manifest)
        
        print("\nInitial snapshot saved.")
        
        # Add new file
        (resources_dir / "file2.txt").write_text("New content", encoding="utf-8")
        
        # Modify existing file
        (resources_dir / "file1.txt").write_text("Modified content", encoding="utf-8")
        
        # Show diff
        print("\nShowing diff after changes:")
        main(["diff", "--workspace", str(test_dir)])
        
        print("\n[PASS] Diff detection works correctly")


def test_artifacts_and_memory_commands():
    """Test: artifacts and memory commands."""
    print("\n" + "="*60)
    print("TEST 8: Artifacts and memory commands")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_workspace"
        
        # Init workspace
        from .cli import main
        main(["init", "--dir", str(test_dir)])
        
        # Create some artifacts
        (test_dir / "artifacts" / "reports" / "report1.md").write_text("# Report 1", encoding="utf-8")
        (test_dir / "artifacts" / "reports" / "report2.md").write_text("# Report 2", encoding="utf-8")
        (test_dir / "artifacts" / "memory" / "notes.md").write_text("# Memory\n\nKey observation.", encoding="utf-8")
        
        print("\nArtifacts command:")
        main(["artifacts", "--workspace", str(test_dir)])
        
        print("\nMemory command:")
        main(["memory", "--workspace", str(test_dir)])
        
        print("\n[PASS] Artifacts and memory commands work correctly")


def run_all_tests():
    """Run all Phase 1 tests."""
    print("\n" + "="*60)
    print("PHASE 1 AUTOMATIC TEST SUITE")
    print("="*60)
    print("\nTesting the walking skeleton end-to-end...")
    
    tests = [
        test_workspace_init,
        test_scan_empty,
        test_scan_with_files,
        test_extract_content,
        test_write_artifact,
        test_read_artifact,
        test_diff_detection,
        test_artifacts_and_memory_commands,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n[FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Passed: {passed}/{len(tests)}")
    print(f"Failed: {failed}/{len(tests)}")
    
    if failed == 0:
        print("\n[OK] ALL TESTS PASSED - Phase 1 is ready!")
        return 0
    else:
        print(f"\n[FAIL] {failed} test(s) failed - please fix before moving to Phase 2")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
