#!/usr/bin/env python3
"""
Blueprint Converter – Convert between JSON and Excel formats.

Usage:
    python tools/blueprint_converter.py json-to-xlsx input.json --out output.xlsx
    python tools/blueprint_converter.py xlsx-to-json input.xlsx --out output.json
    python tools/blueprint_converter.py validate input.json
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    XLSX_AVAILABLE = True
except ImportError:
    XLSX_AVAILABLE = False


def load_json_blueprint(file_path: str) -> Dict[str, Any]:
    """Load a blueprint from JSON file."""
    with open(file_path, 'r') as f:
        return json.load(f)


def save_json_blueprint(blueprint: Dict[str, Any], file_path: str) -> None:
    """Save a blueprint to JSON file."""
    with open(file_path, 'w') as f:
        json.dump(blueprint, f, indent=2)
    print(f"✅ Saved to {file_path}")


def json_to_xlsx(input_file: str, output_file: str) -> None:
    """Convert JSON blueprint to Excel."""
    if not XLSX_AVAILABLE:
        print("❌ openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    blueprint = load_json_blueprint(input_file)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Metadata sheet
    ws = wb.create_sheet("Metadata")
    ws['A1'] = "Key"
    ws['B1'] = "Value"
    ws['A1'].font = Font(bold=True, color="FFFFFF")
    ws['B1'].font = Font(bold=True, color="FFFFFF")
    ws['A1'].fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    ws['B1'].fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    meta = blueprint.get("meta", {})
    row = 2
    for key, value in meta.items():
        ws[f'A{row}'] = key
        ws[f'B{row}'] = str(value)
        row += 1

    # Departments sheet
    ws = wb.create_sheet("Departments")
    headers = ["ID", "Name", "Owner", "Members", "Description", "Processes", "Tools"]
    for col, header in enumerate(headers, 1):
        ws.cell(1, col).value = header
        ws.cell(1, col).font = Font(bold=True, color="FFFFFF")
        ws.cell(1, col).fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    for row, dept in enumerate(blueprint.get("departments", []), 2):
        ws.cell(row, 1).value = dept.get("id", "")
        ws.cell(row, 2).value = dept.get("name", "")
        ws.cell(row, 3).value = dept.get("owner", "")
        ws.cell(row, 4).value = dept.get("members", "")
        ws.cell(row, 5).value = dept.get("description", "")
        ws.cell(row, 6).value = ", ".join(dept.get("processes", []))
        ws.cell(row, 7).value = ", ".join(dept.get("tools", []))

    # Processes sheet
    ws = wb.create_sheet("Processes")
    headers = ["ID", "Name", "Category", "Owner", "Description", "Frequency", "Metric", "Target", "Current"]
    for col, header in enumerate(headers, 1):
        ws.cell(1, col).value = header
        ws.cell(1, col).font = Font(bold=True, color="FFFFFF")
        ws.cell(1, col).fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    for row, proc in enumerate(blueprint.get("processes", []), 2):
        ws.cell(row, 1).value = proc.get("id", "")
        ws.cell(row, 2).value = proc.get("name", "")
        ws.cell(row, 3).value = proc.get("category", "")
        ws.cell(row, 4).value = proc.get("owner", "")
        ws.cell(row, 5).value = proc.get("description", "")
        ws.cell(row, 6).value = proc.get("frequency", "")
        kpi = proc.get("kpi", {})
        ws.cell(row, 7).value = kpi.get("metric", "")
        ws.cell(row, 8).value = kpi.get("target", "")
        ws.cell(row, 9).value = kpi.get("current", "")

    # Tools sheet
    ws = wb.create_sheet("Tools")
    headers = ["ID", "Name", "Category", "Purpose", "Cost", "Frequency", "Owner", "Criticality", "Users", "Dependencies"]
    for col, header in enumerate(headers, 1):
        ws.cell(1, col).value = header
        ws.cell(1, col).font = Font(bold=True, color="FFFFFF")
        ws.cell(1, col).fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    for row, tool in enumerate(blueprint.get("tools", []), 2):
        cost = tool.get("cost", {})
        ws.cell(row, 1).value = tool.get("id", "")
        ws.cell(row, 2).value = tool.get("name", "")
        ws.cell(row, 3).value = tool.get("category", "")
        ws.cell(row, 4).value = tool.get("purpose", "")
        ws.cell(row, 5).value = cost.get("amount", "")
        ws.cell(row, 6).value = cost.get("frequency", "")
        ws.cell(row, 7).value = tool.get("owner", "")
        ws.cell(row, 8).value = tool.get("criticality", "")
        ws.cell(row, 9).value = ", ".join(tool.get("users", []))
        ws.cell(row, 10).value = ", ".join(tool.get("dependencies", []))

    # Changes sheet
    ws = wb.create_sheet("Changes")
    headers = ["ID", "Date", "Title", "Category", "Status", "Description", "Author"]
    for col, header in enumerate(headers, 1):
        ws.cell(1, col).value = header
        ws.cell(1, col).font = Font(bold=True, color="FFFFFF")
        ws.cell(1, col).fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    for row, change in enumerate(blueprint.get("changes", []), 2):
        ws.cell(row, 1).value = change.get("id", "")
        ws.cell(row, 2).value = change.get("date", "")
        ws.cell(row, 3).value = change.get("title", "")
        ws.cell(row, 4).value = change.get("category", "")
        ws.cell(row, 5).value = change.get("status", "")
        ws.cell(row, 6).value = change.get("description", "")
        ws.cell(row, 7).value = change.get("author", "")

    # Roadmap sheet
    ws = wb.create_sheet("Roadmap")
    headers = ["ID", "Title", "Category", "Priority", "Target Date", "Owner", "Status", "Description"]
    for col, header in enumerate(headers, 1):
        ws.cell(1, col).value = header
        ws.cell(1, col).font = Font(bold=True, color="FFFFFF")
        ws.cell(1, col).fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    for row, item in enumerate(blueprint.get("roadmap", []), 2):
        ws.cell(row, 1).value = item.get("id", "")
        ws.cell(row, 2).value = item.get("title", "")
        ws.cell(row, 3).value = item.get("category", "")
        ws.cell(row, 4).value = item.get("priority", "")
        ws.cell(row, 5).value = item.get("targetDate", "")
        ws.cell(row, 6).value = item.get("owner", "")
        ws.cell(row, 7).value = item.get("status", "")
        ws.cell(row, 8).value = item.get("description", "")

    # Auto-adjust column widths
    for ws in wb.sheetnames:
        sheet = wb[ws]
        for column in sheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            sheet.column_dimensions[column_letter].width = adjusted_width

    wb.save(output_file)
    print(f"✅ Converted to Excel: {output_file}")


def xlsx_to_json(input_file: str, output_file: str) -> None:
    """Convert Excel blueprint to JSON."""
    if not XLSX_AVAILABLE:
        print("❌ openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(input_file)

    blueprint = {
        "meta": {},
        "departments": [],
        "processes": [],
        "tools": [],
        "changes": [],
        "roadmap": []
    }

    # Read metadata
    if "Metadata" in wb.sheetnames:
        ws = wb["Metadata"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] and row[1]:
                blueprint["meta"][row[0]] = row[1]

    # Ensure required meta fields
    if "created" not in blueprint["meta"]:
        blueprint["meta"]["created"] = datetime.now().isoformat()
    if "lastModified" not in blueprint["meta"]:
        blueprint["meta"]["lastModified"] = datetime.now().isoformat()

    # Read departments
    if "Departments" in wb.sheetnames:
        ws = wb["Departments"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0]:
                blueprint["departments"].append({
                    "id": row[0] or "",
                    "name": row[1] or "",
                    "owner": row[2] or "",
                    "members": row[3] or 1,
                    "description": row[4] or "",
                    "processes": [p.strip() for p in (row[5] or "").split(",") if p.strip()],
                    "tools": [t.strip() for t in (row[6] or "").split(",") if t.strip()]
                })

    # Read processes
    if "Processes" in wb.sheetnames:
        ws = wb["Processes"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0]:
                blueprint["processes"].append({
                    "id": row[0] or "",
                    "name": row[1] or "",
                    "category": row[2] or "",
                    "owner": row[3] or "",
                    "description": row[4] or "",
                    "frequency": row[5] or "",
                    "steps": [],
                    "kpi": {
                        "metric": row[6] or "",
                        "target": row[7] or "",
                        "current": row[8] or ""
                    }
                })

    # Read tools
    if "Tools" in wb.sheetnames:
        ws = wb["Tools"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0]:
                blueprint["tools"].append({
                    "id": row[0] or "",
                    "name": row[1] or "",
                    "category": row[2] or "",
                    "purpose": row[3] or "",
                    "cost": {
                        "amount": row[4] or 0,
                        "currency": "USD",
                        "frequency": row[5] or "one-time"
                    },
                    "owner": row[6] or "",
                    "criticality": row[7] or "important",
                    "users": [u.strip() for u in (row[8] or "").split(",") if u.strip()],
                    "dependencies": [d.strip() for d in (row[9] or "").split(",") if d.strip()]
                })

    # Read changes
    if "Changes" in wb.sheetnames:
        ws = wb["Changes"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0]:
                blueprint["changes"].append({
                    "id": row[0] or "",
                    "date": str(row[1] or ""),
                    "title": row[2] or "",
                    "category": row[3] or "",
                    "status": row[4] or "completed",
                    "description": row[5] or "",
                    "author": row[6] or ""
                })

    # Read roadmap
    if "Roadmap" in wb.sheetnames:
        ws = wb["Roadmap"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0]:
                blueprint["roadmap"].append({
                    "id": row[0] or "",
                    "title": row[1] or "",
                    "category": row[2] or "",
                    "priority": row[3] or "medium",
                    "targetDate": str(row[4] or ""),
                    "owner": row[5] or "",
                    "status": row[6] or "backlog",
                    "description": row[7] or ""
                })

    save_json_blueprint(blueprint, output_file)
    print(f"✅ Converted to JSON: {output_file}")


def validate_blueprint(file_path: str) -> None:
    """Validate a blueprint against the schema."""
    blueprint = load_json_blueprint(file_path)

    errors = []
    warnings = []

    # Required fields in meta
    if not blueprint.get("meta", {}).get("name"):
        errors.append("❌ meta.name is required")
    if not blueprint.get("meta", {}).get("owner"):
        errors.append("❌ meta.owner is required")

    # At least one department
    if len(blueprint.get("departments", [])) == 0:
        warnings.append("⚠️  No departments defined")

    # At least one process
    if len(blueprint.get("processes", [])) == 0:
        warnings.append("⚠️  No processes defined")

    # Check all processes have owners
    for proc in blueprint.get("processes", []):
        if not proc.get("owner"):
            errors.append(f"❌ Process '{proc.get('name', '?')}' has no owner")

    # Check all departments have owners
    for dept in blueprint.get("departments", []):
        if not dept.get("owner"):
            errors.append(f"❌ Department '{dept.get('name', '?')}' has no owner")

    # Check all tools have owners
    for tool in blueprint.get("tools", []):
        if not tool.get("owner"):
            errors.append(f"❌ Tool '{tool.get('name', '?')}' has no owner")

    # Print results
    if errors:
        print("\n🔴 VALIDATION ERRORS:")
        for error in errors:
            print(error)
        sys.exit(1)

    if warnings:
        print("\n🟡 WARNINGS:")
        for warning in warnings:
            print(warning)

    # Summary
    print("\n✅ BLUEPRINT VALID")
    print(f"   Departments: {len(blueprint.get('departments', []))}")
    print(f"   Processes: {len(blueprint.get('processes', []))}")
    print(f"   Tools: {len(blueprint.get('tools', []))}")
    print(f"   Changes logged: {len(blueprint.get('changes', []))}")
    print(f"   Roadmap items: {len(blueprint.get('roadmap', []))}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    input_file = sys.argv[2] if len(sys.argv) > 2 else None
    output_file = None

    if "--out" in sys.argv:
        out_idx = sys.argv.index("--out")
        output_file = sys.argv[out_idx + 1] if out_idx + 1 < len(sys.argv) else None

    if command == "json-to-xlsx":
        if not input_file or not output_file:
            print("Usage: blueprint_converter.py json-to-xlsx input.json --out output.xlsx")
            sys.exit(1)
        json_to_xlsx(input_file, output_file)

    elif command == "xlsx-to-json":
        if not input_file or not output_file:
            print("Usage: blueprint_converter.py xlsx-to-json input.xlsx --out output.json")
            sys.exit(1)
        xlsx_to_json(input_file, output_file)

    elif command == "validate":
        if not input_file:
            print("Usage: blueprint_converter.py validate input.json")
            sys.exit(1)
        validate_blueprint(input_file)

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
