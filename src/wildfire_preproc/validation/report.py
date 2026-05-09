"""Stage 6 — human-readable validation report."""

from __future__ import annotations

from wildfire_preproc.validation.checks import ValidationResult


def format_report(results: list[ValidationResult], manifest_ok: bool) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("VALIDATION REPORT")
    lines.append("=" * 72)
    lines.append(f"  {'manifest':<22} [{'PASS' if manifest_ok else 'FAIL'}]")
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        lines.append(f"  {r.layer:<22} [{status}]   {r.path.name}")
        for e in r.errors:
            lines.append(f"    - {e}")
    overall = manifest_ok and all(r.ok for r in results)
    lines.append("-" * 72)
    lines.append(f"overall: {'PASS' if overall else 'FAIL'}")
    lines.append("=" * 72)
    return "\n".join(lines)
