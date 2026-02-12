"""vCard import/export — export contacts to vCard 3.0 and import from vCard."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship.addresses import address_add, address_list
from butlers.tools.relationship.contact_info import contact_info_add, contact_info_list
from butlers.tools.relationship.contacts import _parse_contact, contact_create, contact_get
from butlers.tools.relationship.dates import date_add, date_list
from butlers.tools.relationship.facts import fact_list, fact_set
from butlers.tools.relationship.notes import note_create, note_list

logger = logging.getLogger(__name__)


def _contact_full_name(contact: dict[str, Any]) -> str:
    first = str(contact.get("first_name") or "").strip()
    last = str(contact.get("last_name") or "").strip()
    nickname = str(contact.get("nickname") or "").strip()
    full = " ".join(part for part in [first, last] if part).strip()
    return full or nickname or first or "Unknown"


async def contact_export_vcard(pool: asyncpg.Pool, contact_id: uuid.UUID | None = None) -> str:
    """Export one or all contacts as vCard 3.0.

    Args:
        pool: Database connection pool
        contact_id: Optional contact ID. If None, exports all listed contacts.

    Returns:
        vCard 3.0 formatted string (multiple vCards if exporting all)
    """
    import vobject

    if contact_id is not None:
        # Export single contact
        contact = await contact_get(pool, contact_id)
        contacts = [contact] if contact is not None else []
    else:
        # Export all listed contacts
        rows = await pool.fetch(
            "SELECT * FROM contacts WHERE listed = true ORDER BY first_name, last_name, nickname"
        )
        contacts = [_parse_contact(row) for row in rows]

    vcards = []
    for contact in contacts:
        if contact is None:
            continue

        vcard = vobject.vCard()

        # FN (Formatted Name) - required field
        full_name = _contact_full_name(contact)
        vcard.add("fn")
        vcard.fn.value = full_name

        # N (Name) - required field
        vcard.add("n")
        vcard.n.value = vobject.vcard.Name(
            family=str(contact.get("last_name") or ""),
            given=str(contact.get("first_name") or ""),
        )

        # TEL/EMAIL - from contact_info table
        infos = await contact_info_list(pool, contact["id"])
        phones = [info for info in infos if info["type"] == "phone"]
        for phone in phones:
            tel = vcard.add("tel")
            tel.value = phone["value"]
            tel.type_param = phone.get("label", "VOICE")

        emails = [info for info in infos if info["type"] == "email"]
        for email in emails:
            email_field = vcard.add("email")
            email_field.value = email["value"]
            email_field.type_param = email.get("label", "INTERNET")

        # ADR (Address) - from addresses table
        addresses = await address_list(pool, contact["id"])
        for addr in addresses:
            adr = vcard.add("adr")
            adr.value = vobject.vcard.Address(
                street=" ".join(
                    part for part in [addr.get("line_1", ""), addr.get("line_2", "")] if part
                ),
                city=addr.get("city", ""),
                region=addr.get("province", ""),
                code=addr.get("postal_code", ""),
                country=addr.get("country", ""),
            )
            adr.type_param = addr.get("label", "HOME")

        # BDAY (Birthday) - from important_dates
        dates = await date_list(pool, contact["id"])
        for date in dates:
            if date["label"].lower() == "birthday":
                vcard.add("bday")
                if date.get("year"):
                    vcard.bday.value = f"{date['year']:04d}-{date['month']:02d}-{date['day']:02d}"
                else:
                    vcard.bday.value = f"--{date['month']:02d}-{date['day']:02d}"
                break

        # ORG (Organization) - from quick_facts.company
        facts = await fact_list(pool, contact["id"])
        facts_dict = {f["key"]: f["value"] for f in facts}

        if "company" in facts_dict:
            vcard.add("org")
            vcard.org.value = [facts_dict["company"]]

        # TITLE (Job Title) - from quick_facts.job_title
        if "job_title" in facts_dict:
            vcard.add("title")
            vcard.title.value = facts_dict["job_title"]

        # NOTE - combine emotion notes if any
        notes = await note_list(pool, contact["id"])
        if notes:
            note_texts = [n["body"] for n in notes[:3]]  # Limit to 3 most recent
            vcard.add("note")
            vcard.note.value = "\n---\n".join(note_texts)

        vcards.append(vcard.serialize())

    return "".join(vcards)


async def contact_import_vcard(pool: asyncpg.Pool, vcf_content: str) -> list[dict[str, Any]]:
    """Import vCard data and create contacts.

    Parses vCard 3.0/4.0 content and creates contacts with:
    - FN/N -> first_name / last_name
    - TEL -> contact_info(type=phone)
    - EMAIL -> contact_info(type=email)
    - ADR -> addresses
    - BDAY -> important_dates (birthday)
    - ORG -> quick_facts (company)
    - TITLE -> quick_facts (job_title)
    - NOTE -> notes

    Args:
        pool: Database connection pool
        vcf_content: vCard formatted string (can contain multiple vCards)

    Returns:
        List of created contact dicts
    """
    import vobject

    created_contacts = []

    # Parse vCard(s) - vobject can handle multiple vCards in one string
    try:
        vcards = vobject.readComponents(vcf_content)
    except (vobject.base.ParseError, Exception) as e:
        raise ValueError(f"Failed to parse vCard content: {e}") from e

    for vcard in vcards:
        # Required: FN (Formatted Name)
        if not hasattr(vcard, "fn"):
            logger.warning("Skipping vCard without FN field")
            continue

        first_name: str | None = None
        last_name: str | None = None
        if hasattr(vcard, "n") and getattr(vcard.n, "value", None):
            first_name = str(getattr(vcard.n.value, "given", "") or "").strip() or None
            last_name = str(getattr(vcard.n.value, "family", "") or "").strip() or None
        if first_name is None and last_name is None:
            first_name = str(vcard.fn.value).strip() or None

        # Create the contact
        contact = await contact_create(pool, first_name=first_name, last_name=last_name)
        created_contacts.append(contact)

        # TEL (Phone numbers) -> contact_info
        if hasattr(vcard, "tel_list"):
            for tel in vcard.tel_list:
                phone_label = "VOICE"
                if hasattr(tel, "type_param") and isinstance(tel.type_param, str):
                    phone_label = tel.type_param
                await contact_info_add(
                    pool,
                    contact["id"],
                    "phone",
                    str(tel.value),
                    label=phone_label,
                )

        # EMAIL -> contact_info
        if hasattr(vcard, "email_list"):
            for email in vcard.email_list:
                email_label = "INTERNET"
                if hasattr(email, "type_param") and isinstance(email.type_param, str):
                    email_label = email.type_param
                await contact_info_add(
                    pool,
                    contact["id"],
                    "email",
                    str(email.value),
                    label=email_label,
                )

        # ADR (Addresses) -> addresses table
        if hasattr(vcard, "adr_list"):
            for adr in vcard.adr_list:
                addr_label = "Home"
                if hasattr(adr, "type_param") and isinstance(adr.type_param, str):
                    addr_label = adr.type_param

                addr_value = adr.value
                street = addr_value.street if hasattr(addr_value, "street") else ""
                await address_add(
                    pool,
                    contact["id"],
                    line_1=str(street or ""),
                    label=addr_label,
                    city=str(addr_value.city if hasattr(addr_value, "city") else "" or ""),
                    province=str(addr_value.region if hasattr(addr_value, "region") else "" or ""),
                    postal_code=str(addr_value.code if hasattr(addr_value, "code") else "" or ""),
                    country=str(addr_value.country if hasattr(addr_value, "country") else "" or "")
                    or None,
                )

        # BDAY (Birthday) -> important_dates
        if hasattr(vcard, "bday"):
            bday_value = vcard.bday.value
            if isinstance(bday_value, str):
                # Parse date string: YYYY-MM-DD or --MM-DD
                parts = bday_value.strip().split("-")
                parts = [p for p in parts if p]  # Remove empty strings

                if len(parts) >= 2:
                    try:
                        if len(parts) == 3:
                            # YYYY-MM-DD
                            year = int(parts[0])
                            month = int(parts[1])
                            day = int(parts[2])
                            await date_add(pool, contact["id"], "birthday", month, day, year)
                        else:
                            # --MM-DD or MM-DD
                            month = int(parts[0])
                            day = int(parts[1])
                            await date_add(pool, contact["id"], "birthday", month, day)
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse birthday '{bday_value}': {e}")
            elif hasattr(bday_value, "year"):
                # Date object
                try:
                    await date_add(
                        pool,
                        contact["id"],
                        "birthday",
                        bday_value.month,
                        bday_value.day,
                        bday_value.year,
                    )
                except Exception as e:
                    logger.warning(f"Failed to add birthday: {e}")

        # ORG (Organization) -> quick_facts.company
        if hasattr(vcard, "org"):
            org_value = vcard.org.value
            if isinstance(org_value, list) and org_value:
                await fact_set(pool, contact["id"], "company", org_value[0])
            elif isinstance(org_value, str):
                await fact_set(pool, contact["id"], "company", org_value)

        # TITLE (Job Title) -> quick_facts.job_title
        if hasattr(vcard, "title"):
            await fact_set(pool, contact["id"], "job_title", vcard.title.value)

        # NOTE -> notes
        if hasattr(vcard, "note"):
            note_value = vcard.note.value
            if note_value:
                # Split on --- if it was exported from our system
                note_parts = note_value.split("\n---\n")
                for note_text in note_parts:
                    if note_text.strip():
                        await note_create(pool, contact["id"], note_text.strip())

    return created_contacts
