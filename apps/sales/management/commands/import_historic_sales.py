"""Import YTD historic sales from the James spreadsheet (Sheet 1 only).

Creates 173 reporting-only sales per valid agent (2026-01-01 .. 2026-06-22),
one per day, using Column F as the annual premium on a Historic Sync add-on line item.

Red-highlighted agents are skipped (not in our database). Yellow agents use the
first Profile match when multiple exist (Collin Fleming).

Every created sale is tagged with ``import_batch_id`` (UUID) for rollback via
``rollback_historic_sales``.

Usage:
    python manage.py import_historic_sales --dry-run
    python manage.py import_historic_sales /path/to/file.xlsx
    python manage.py rollback_historic_sales <batch-uuid>
"""

from __future__ import annotations

import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.authentication.models import Profile
from apps.catalog.models import AddOn
from apps.company.models import CompanySettings
from apps.ghl.models import GhlContact
from apps.sales.models import Sale

DEFAULT_XLSX = (
    Path.home()
    / "Downloads"
    / "james 01-01-2026 - 22-06-2026 173 days.xlsx"
)
GHL_CONTACT_ID = "J936WEzMvB4ZxQRQgpVE"
ADDON_NAME = "Historic Sync"
IMPORT_DAYS = 173
START_DATE = date(2026, 1, 1)
SHEET_XML = "xl/worksheets/sheet1.xml"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass
class SheetRow:
    row_num: int
    agent_name: str
    daily_amount: Decimal
    color_class: str  # "red" | "yellow" | "none"


def _col_row(ref: str) -> tuple[str, int]:
    col = "".join(c for c in ref if c.isalpha())
    row = int("".join(c for c in ref if c.isdigit()))
    return col, row


def _rgb_to_class(rgb: str | None) -> str | None:
    if not rgb:
        return None
    r = rgb.upper()
    if r.endswith("FF0000"):
        return "red"
    if r.endswith("FFFF00") or r.endswith("FFE033"):
        return "yellow"
    return None


def _parse_styles(xlsx: zipfile.ZipFile) -> list[str | None]:
    styles = ET.fromstring(xlsx.read("xl/styles.xml"))
    fills: list[str | None] = []
    for fill in styles.find("m:fills", NS).findall("m:fill", NS):
        pattern = fill.find("m:patternFill", NS)
        fg = pattern.find("m:fgColor", NS) if pattern is not None else None
        rgb = fg.get("rgb") if fg is not None else None
        fills.append(_rgb_to_class(rgb))
    xfs_fill: list[str | None] = []
    for xf in styles.find("m:cellXfs", NS).findall("m:xf", NS):
        fill_id = int(xf.get("fillId", 0))
        xfs_fill.append(fills[fill_id] if fill_id < len(fills) else None)
    return xfs_fill


def _load_shared_strings(xlsx: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in xlsx.namelist():
        return []
    root = ET.fromstring(xlsx.read("xl/sharedStrings.xml"))
    return [
        "".join(t.text or "" for t in si.findall(".//m:t", NS))
        for si in root.findall("m:si", NS)
    ]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    v = cell.find("m:v", NS)
    if v is None:
        return ""
    if cell.get("t") == "s":
        return shared[int(v.text)]
    return v.text or ""


def _row_color_class(row_el: ET.Element, xfs_fill: list[str | None]) -> str:
    colors: set[str] = set()
    for cell in row_el.findall("m:c", NS):
        style_idx = int(cell.get("s", 0))
        if style_idx < len(xfs_fill) and xfs_fill[style_idx]:
            colors.add(xfs_fill[style_idx])
    if "red" in colors:
        return "red"
    if "yellow" in colors:
        return "yellow"
    return "none"


def parse_sheet1(path: Path) -> list[SheetRow]:
    if not path.exists():
        raise CommandError(f"Spreadsheet not found: {path}")

    rows: list[SheetRow] = []
    with zipfile.ZipFile(path) as xlsx:
        if SHEET_XML not in xlsx.namelist():
            raise CommandError(f"{SHEET_XML} not found in workbook.")
        xfs_fill = _parse_styles(xlsx)
        shared = _load_shared_strings(xlsx)
        root = ET.fromstring(xlsx.read(SHEET_XML))

        for row_el in root.findall(".//m:sheetData/m:row", NS):
            row_num = int(row_el.get("r"))
            if row_num == 1:
                continue

            data: dict[str, str] = {}
            for cell in row_el.findall("m:c", NS):
                ref = cell.get("r")
                col, _ = _col_row(ref)
                data[col] = _cell_value(cell, shared)

            agent = (data.get("A") or "").strip()
            if not agent:
                continue

            raw_amount = (data.get("F") or "").strip()
            if not raw_amount:
                raise CommandError(f"Row {row_num} ({agent}): Column F is empty.")

            try:
                daily_amount = Decimal(raw_amount)
            except InvalidOperation as exc:
                raise CommandError(
                    f"Row {row_num} ({agent}): invalid Column F value {raw_amount!r}."
                ) from exc

            rows.append(
                SheetRow(
                    row_num=row_num,
                    agent_name=agent,
                    daily_amount=daily_amount,
                    color_class=_row_color_class(row_el, xfs_fill),
                )
            )
    return rows


def _reporting_tz() -> ZoneInfo:
    settings_row = CompanySettings.objects.first()
    tz_name = settings_row.reporting_timezone if settings_row else "America/New_York"
    return ZoneInfo(tz_name)


def _sale_dates() -> list[datetime]:
    tz = _reporting_tz()
    return [
        datetime(
            (START_DATE + timedelta(days=i)).year,
            (START_DATE + timedelta(days=i)).month,
            (START_DATE + timedelta(days=i)).day,
            12,
            0,
            0,
            tzinfo=tz,
        )
        for i in range(IMPORT_DAYS)
    ]


def _line_item(amount: Decimal) -> list[dict]:
    return [
        {
            "kind": "addon",
            "carrier": "Add-on",
            "product": ADDON_NAME,
            "amount": float(amount),
        }
    ]


class Command(BaseCommand):
    help = "Import historic YTD sales from the James spreadsheet (Sheet 1)."

    def add_arguments(self, parser):
        parser.add_argument(
            "xlsx_path",
            nargs="?",
            default=str(DEFAULT_XLSX),
            help="Path to the .xlsx workbook (Sheet 1 is read).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report counts without writing.",
        )
        parser.add_argument(
            "--batch-id",
            default="",
            help="Reuse a specific import batch UUID (must not already exist in sales).",
        )
        parser.add_argument(
            "--only-agents",
            default="",
            help="Comma-separated agent names to import (case-insensitive exact match on sheet Column A).",
        )

    def handle(self, *args, **options):
        path = Path(options["xlsx_path"]).expanduser()
        dry_run = options["dry_run"]

        if options["batch_id"]:
            try:
                batch_id = uuid.UUID(options["batch_id"])
            except ValueError as exc:
                raise CommandError(f"Invalid --batch-id: {options['batch_id']}") from exc
        else:
            batch_id = uuid.uuid4()

        if Sale.objects.filter(import_batch_id=batch_id).exists():
            raise CommandError(
                f"Batch {batch_id} already has sales. "
                "Roll back first or omit --batch-id to generate a new UUID."
            )

        sheet_rows = parse_sheet1(path)
        red_rows = [r for r in sheet_rows if r.color_class == "red"]
        import_rows = [r for r in sheet_rows if r.color_class != "red"]

        if options["only_agents"]:
            allowed = {a.strip().lower() for a in options["only_agents"].split(",") if a.strip()}
            import_rows = [r for r in import_rows if r.agent_name.lower() in allowed]
            if not import_rows:
                raise CommandError(
                    f"No importable rows matched --only-agents: {options['only_agents']}"
                )

        contact = GhlContact.objects.filter(id=GHL_CONTACT_ID).first()
        if contact is None:
            raise CommandError(f"GHL contact {GHL_CONTACT_ID} not found in database.")

        addon, created = AddOn.objects.get_or_create(
            name=ADDON_NAME,
            defaults={"active": True},
        )
        if created:
            self.stdout.write(self.style.WARNING(f"Created add-on: {ADDON_NAME}"))

        dates = _sale_dates()
        end_date = START_DATE + timedelta(days=IMPORT_DAYS - 1)

        agent_plans: list[tuple[SheetRow, Profile]] = []
        skipped_red = len(red_rows)
        skipped_no_profile: list[SheetRow] = []
        warnings: list[str] = []

        for row in import_rows:
            profiles = list(
                Profile.objects.select_related("user", "team")
                .filter(display_name__iexact=row.agent_name)
                .order_by("user_id")
            )
            if not profiles:
                skipped_no_profile.append(row)
                warnings.append(
                    f"Row {row.row_num} ({row.agent_name}): no Profile match — skipped."
                )
                continue
            if len(profiles) > 1:
                warnings.append(
                    f"Row {row.row_num} ({row.agent_name}): {len(profiles)} profiles; "
                    f"using {profiles[0].display_name} ({profiles[0].user_id})."
                )
            agent_plans.append((row, profiles[0]))

        if not agent_plans:
            raise CommandError("No agents matched — nothing to import.")

        total_sales = len(agent_plans) * IMPORT_DAYS

        self.stdout.write(f"Workbook: {path}")
        self.stdout.write(f"Batch ID: {batch_id}")
        self.stdout.write(f"Date range: {START_DATE} .. {end_date} ({IMPORT_DAYS} days)")
        self.stdout.write(f"GHL contact: {contact.name} ({contact.id})")
        self.stdout.write(f"Add-on: {addon.name}")
        self.stdout.write(f"Agents to import: {len(agent_plans)}")
        self.stdout.write(f"Red rows skipped: {skipped_red}")
        self.stdout.write(f"No-profile rows skipped: {len(skipped_no_profile)}")
        self.stdout.write(f"Sales to create: {total_sales}")

        for msg in warnings:
            self.stdout.write(self.style.WARNING(msg))

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete — no changes written."))
            return

        sale_dates = dates
        to_create: list[Sale] = []
        seq = 0

        for row, profile in agent_plans:
            team = profile.team
            for sale_dt in sale_dates:
                seq += 1
                sale_id = f"HIST-{batch_id.hex[:8].upper()}-{seq:07d}"
                to_create.append(
                    Sale(
                        sale_id=sale_id,
                        agent=profile.user,
                        agent_name=row.agent_name,
                        team=team,
                        team_name=team.name if team else None,
                        sale_date=sale_dt,
                        customer_name=contact.name,
                        ghl_contact_id=GHL_CONTACT_ID,
                        deal_size=row.daily_amount,
                        carrier="Add-on",
                        product=ADDON_NAME,
                        add_ons=[],
                        add_on_amounts={},
                        line_items=_line_item(row.daily_amount),
                        lead_source=None,
                        cost_per_lead=None,
                        notes=f"Historic YTD import batch {batch_id}",
                        reporting_only=True,
                        import_batch_id=batch_id,
                    )
                )

        self.stdout.write(f"Inserting {len(to_create)} sales …")
        with transaction.atomic():
            Sale.objects.bulk_create(to_create, batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(to_create)} sales (batch {batch_id}). "
                f"Rollback: python manage.py rollback_historic_sales {batch_id}"
            )
        )
