"""TallyPrime XML request builders.

The collection requests are read-only exports. `build_voucher_import_request`
is the one write path: it posts a single approved voucher. It carries PAL's
voucher id as Tally's `REMOTEID`, so Tally matches a retry to the voucher it
already holds instead of creating a second one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from xml.sax.saxutils import escape

from app.integrations.tally.exceptions import TallyConfigurationError

_COMPANY_COLLECTION_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Companies</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        <SVIsSimpleCompany>No</SVIsSimpleCompany>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="List of Companies" ISMODIFY="No" ISFIXED="No" ISINITIALIZE="Yes" ISOPTION="No" ISINTERNAL="No">
            <TYPE>Company</TYPE>
            <NATIVEMETHOD>Name</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""

_LEDGER_COLLECTION_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>Ledgers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {current_company}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="Ledgers" ISMODIFY="No" ISFIXED="No" ISINITIALIZE="No" ISOPTION="No" ISINTERNAL="No">
            <TYPE>Ledger</TYPE>
            <FETCH>Name, Parent, ClosingBalance, PARTYGSTIN, LEDGSTREGDETAILS.LIST</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""


_VOUCHER_COLLECTION_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>Vouchers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {current_company}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="Vouchers" ISMODIFY="No" ISFIXED="No" ISINITIALIZE="No" ISOPTION="No" ISINTERNAL="No">
            <TYPE>Voucher</TYPE>
            <FETCH>DATE,VOUCHERTYPENAME,VOUCHERNUMBER,NARRATION,GUID,MASTERID,PARTYLEDGERNAME,AMOUNT,ALLLEDGERENTRIES.LEDGERNAME,ALLLEDGERENTRIES.AMOUNT,ALLLEDGERENTRIES.ISDEEMEDPOSITIVE{inventory_fetch}</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""


_VOUCHER_IMPORT_REQUEST = """<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>{current_company}</STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
{voucher}
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


@dataclass(frozen=True)
class VoucherImportLine:
    """One ledger line to post. `is_debit` fixes the sign Tally expects."""

    ledger_name: str
    amount: Decimal
    is_debit: bool


def build_voucher_import_request(
    *,
    company: str | None,
    voucher_date: str,
    voucher_type: str,
    voucher_number: str,
    narration: str | None,
    lines: list[VoucherImportLine],
    remote_id: str,
    reference: str | None = None,
    reference_date: str | None = None,
) -> str:
    """Return the XML that imports one voucher into TallyPrime.

    `voucher_date` is Tally's own YYYYMMDD form. `remote_id` is PAL's voucher
    id: Tally treats REMOTEID as the external identity of the voucher, so
    re-sending the same id updates that voucher rather than adding another.

    Amount signs follow Tally's convention and are never recalculated here: a
    debit is negative with ISDEEMEDPOSITIVE Yes, a credit positive with No.
    """
    if not lines:
        raise ValueError("A voucher import needs at least one ledger line.")

    entries = []
    for line in lines:
        magnitude = abs(line.amount)
        signed = -magnitude if line.is_debit else magnitude
        entries.append(
            "          <ALLLEDGERENTRIES.LIST>\n"
            f"            <LEDGERNAME>{escape(line.ledger_name)}</LEDGERNAME>\n"
            "            <ISDEEMEDPOSITIVE>"
            f"{'Yes' if line.is_debit else 'No'}"
            "</ISDEEMEDPOSITIVE>\n"
            f"            <AMOUNT>{signed}</AMOUNT>\n"
            "          </ALLLEDGERENTRIES.LIST>"
        )

    narration_xml = (
        f"            <NARRATION>{escape(narration)}</NARRATION>\n"
        if narration and narration.strip()
        else ""
    )
    # Supplier invoice No./Date in Tally: REFERENCE is the original bill number,
    # REFERENCEDATE its original date (YYYYMMDD), independent of the voucher DATE.
    reference_xml = (
        f"            <REFERENCE>{escape(reference)}</REFERENCE>\n"
        if reference and reference.strip()
        else ""
    )
    reference_date_xml = (
        f"            <REFERENCEDATE>{escape(reference_date)}</REFERENCEDATE>\n"
        if reference_date and reference_date.strip()
        else ""
    )
    voucher_xml = (
        f'        <VOUCHER REMOTEID="{escape(remote_id)}" '
        f'VCHTYPE="{escape(voucher_type)}" ACTION="Create" '
        'OBJVIEW="Accounting Voucher View">\n'
        f"            <DATE>{escape(voucher_date)}</DATE>\n"
        f"            <VOUCHERTYPENAME>{escape(voucher_type)}</VOUCHERTYPENAME>\n"
        f"            <VOUCHERNUMBER>{escape(voucher_number)}</VOUCHERNUMBER>\n"
        f"{reference_xml}"
        f"{reference_date_xml}"
        # Match TallyPrime's own "As Voucher" ledger-entry mode, under which it
        # binds REFERENCE/REFERENCEDATE to the Supplier Invoice No./Date fields.
        "            <VCHENTRYMODE>As Voucher</VCHENTRYMODE>\n"
        # Force accounting-voucher (ledger) mode. Without this Tally posts a
        # Purchase/Sales voucher in invoice (item) mode and expects inventory
        # entries, which PAL does not send.
        "            <ISINVOICE>No</ISINVOICE>\n"
        f"{narration_xml}"
        + "\n".join(entries)
        + "\n        </VOUCHER>"
    )
    current_company = ""
    if company:
        current_company = (
            f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        )
    return _VOUCHER_IMPORT_REQUEST.format(
        current_company=current_company, voucher=voucher_xml
    )


_MASTER_IMPORT_REQUEST = """<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>All Masters</REPORTNAME>
        <STATICVARIABLES>{current_company}</STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
{master}
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


def build_ledger_import_request(
    *,
    company: str | None,
    name: str,
    parent: str,
    gstin: str | None = None,
) -> str:
    """Return the XML that creates one ledger master in TallyPrime.

    `parent` is the Tally group the ledger sits under (e.g. "Sundry Creditors").
    When `gstin` is given the party is marked a regular GST registration so the
    GSTIN is stored on the master. Used to mirror a PAL ledger/vendor into Tally.
    """
    if not name or not name.strip():
        raise TallyConfigurationError("Ledger name must not be blank.")
    if not parent or not parent.strip():
        raise TallyConfigurationError("Ledger parent group must not be blank.")
    name = name.strip()
    gst_xml = ""
    if gstin and gstin.strip():
        gst_xml = (
            "          <GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>\n"
            f"          <PARTYGSTIN>{escape(gstin.strip())}</PARTYGSTIN>\n"
        )
    master = (
        f'        <LEDGER NAME="{escape(name)}" ACTION="Create">\n'
        f"          <NAME.LIST><NAME>{escape(name)}</NAME></NAME.LIST>\n"
        f"          <PARENT>{escape(parent.strip())}</PARENT>\n"
        f"{gst_xml}"
        "        </LEDGER>"
    )
    current_company = ""
    if company:
        current_company = f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
    return _MASTER_IMPORT_REQUEST.format(
        current_company=current_company, master=master
    )


def build_company_collection_request() -> str:
    """Return the XML request that lists companies loaded in TallyPrime."""
    return _COMPANY_COLLECTION_REQUEST


def build_ledger_collection_request(company: str | None = None) -> str:
    """Return the XML request that lists ledgers for one Tally company.

    When ``company`` is omitted TallyPrime uses its currently selected
    company. The caller can pass an explicit name to avoid ambiguity when
    several companies are loaded.
    """
    current_company = ""
    if company:
        current_company = (
            f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        )
    return _LEDGER_COLLECTION_REQUEST.format(current_company=current_company)


_VOUCHER_REGISTER_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>Voucher Register</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        <EXPLODEFLAG>No</EXPLODEFLAG>
        {current_company}
        {from_date}
        {to_date}
      </STATICVARIABLES>
    </DESC>
  </BODY>
</ENVELOPE>"""


def build_voucher_register_request(
    company: str | None = None,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """Return the Voucher Register request for a date range.

    Unlike the plain voucher collection, this report exposes each voucher's
    REFERENCE (the Supplier Invoice No.), and unlike the Day Book it honours
    SVFROMDATE/SVTODATE. Dates are Tally's YYYYMMDD form. Read-only.
    """
    current_company = (
        f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>" if company else ""
    )
    from_xml = f"<SVFROMDATE>{escape(from_date)}</SVFROMDATE>" if from_date else ""
    to_xml = f"<SVTODATE>{escape(to_date)}</SVTODATE>" if to_date else ""
    return _VOUCHER_REGISTER_REQUEST.format(
        current_company=current_company, from_date=from_xml, to_date=to_xml
    )


def build_voucher_collection_request(
    company: str | None = None, *, with_inventory: bool = False
) -> str:
    """Return the XML request that reads accounting vouchers for one company.

    The request is read-only: it exports vouchers with their ledger entries so
    the bank sync can find bank-ledger movements. It never posts, alters or
    deletes anything in TallyPrime.

    With ``with_inventory`` each voucher also carries its stock item names, used
    to join a sale to its HSN code. This pulls the full inventory block, so it is
    requested only for the GST export, not the lighter syncs.

    TallyPrime ignores ``SVFROMDATE``/``SVTODATE`` for a plain collection
    export, so PAL filters the returned vouchers by date itself.
    """
    current_company = ""
    if company:
        current_company = (
            f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        )
    return _VOUCHER_COLLECTION_REQUEST.format(
        current_company=current_company,
        inventory_fetch=",ALLINVENTORYENTRIES.STOCKITEMNAME" if with_inventory else "",
    )


_STOCK_ITEM_COLLECTION_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>StockItems</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {current_company}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="StockItems" ISMODIFY="No" ISFIXED="No" ISINITIALIZE="No" ISOPTION="No" ISINTERNAL="No">
            <TYPE>StockItem</TYPE>
            <FETCH>Name,HSNDETAILS.LIST</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""


def build_stock_item_collection_request(company: str | None = None) -> str:
    """Return the read-only XML request that lists stock items with their HSN."""
    current_company = ""
    if company:
        current_company = (
            f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        )
    return _STOCK_ITEM_COLLECTION_REQUEST.format(current_company=current_company)


