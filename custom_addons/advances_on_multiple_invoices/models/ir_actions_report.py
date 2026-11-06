"""Stable, repeated identity block for distributed payment receipt PDFs."""

import base64
from datetime import date
from io import BytesIO

from lxml import html as lxml_html
from PyPDF2 import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

from odoo import models


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def _prepare_html(self, html, report_model=False):
        prepared = super()._prepare_html(
            html, report_model=report_model
        )
        if not (
            self.env.context.get("compact_receipt_pdf_overlay")
            and self.report_name == "account.report_payment_receipt"
        ):
            return prepared

        bodies, res_ids, _header, footer, paperformat_args = prepared

        # Odoo.sh can load the new Python before the QWeb views are upgraded.
        # Drop any old company header here as well, so it cannot appear beneath
        # the repeated PDF identity block.
        receipt_bodies = []
        for body in bodies:
            root = lxml_html.fromstring(body)
            title = root.xpath(
                "//div[contains(concat(' ', normalize-space(@class), ' '), ' page ')]/h3[1]"
            )
            if title:
                title[0].drop_tree()
            receipt_bodies.append(lxml_html.tostring(root, encoding="unicode"))
        paperformat_args["data-report-margin-top"] = "44"
        paperformat_args.pop("data-report-header-spacing", None)
        return receipt_bodies, res_ids, None, footer, paperformat_args

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self._get_report(report_ref)
        if (
            report.report_name != "account.report_payment_receipt"
            or not res_ids
            or len(res_ids) != 1
        ):
            return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

        payment = self.env["account.payment"].browse(res_ids[0])
        if not payment.exists() or not payment.multiple_invoice_distribution:
            return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

        pdf, report_type = super(
            IrActionsReport, self.with_context(compact_receipt_pdf_overlay=True)
        )._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        if report_type != "pdf":
            return pdf, report_type
        return self._add_distributed_receipt_identity(pdf, payment), report_type

    def _add_distributed_receipt_identity(self, pdf, payment):
        source = PdfReader(BytesIO(pdf), strict=False)
        writer = PdfWriter()
        overlays = {}
        for page in source.pages:
            size = (float(page.mediabox.width), float(page.mediabox.height))
            if size not in overlays:
                overlays[size] = PdfReader(
                    BytesIO(self._make_distributed_receipt_identity_overlay(payment, size)),
                    strict=False,
                ).pages[0]
            page.merge_page(overlays[size])
            writer.add_page(page)
        result = BytesIO()
        writer.write(result)
        return result.getvalue()

    def _make_distributed_receipt_identity_overlay(self, payment, page_size):
        width, height = page_size
        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=page_size)
        is_remission = payment.distributed_document_kind == "remission"
        if is_remission:
            issuer = payment.distributed_group_distribution_ids.invoice_id.remission_issuer_partner_id[:1].sudo()
            identity = issuer or payment.company_id.sudo()
            logo = issuer.image_128 if issuer else False
        else:
            identity = payment.company_id.sudo()
            logo = identity.logo

        logo_width = 0
        logo_bottom = height - 30
        if logo:
            try:
                image = ImageReader(BytesIO(base64.b64decode(logo)))
                image_width, image_height = image.getSize()
                logo_width = min(145, image_width * min(145 / image_width, 63 / image_height))
                logo_height = image_height * logo_width / image_width
                logo_bottom -= logo_height
                pdf.drawImage(
                    image, 31, logo_bottom,
                    width=logo_width, height=logo_height, mask="auto",
                )
            except (TypeError, ValueError, OSError):
                logo_width = 0

        company_center = width * 0.485
        company_width = width * 0.32
        name = identity.name if identity else "Emisor de remisión no registrado"
        pdf.setFont("Helvetica-Bold", 9)
        name_lines = simpleSplit(name or "", "Helvetica-Bold", 9, company_width)[:2]
        y = height - 38
        for line in name_lines:
            pdf.drawCentredString(company_center, y, line)
            y -= 10

        address = identity.partner_id if not is_remission and identity else identity
        address_extras = payment._get_receipt_payer_address_extras(address) if address else {}
        municipality = address_extras.get("municipality")
        address_top = ", ".join(filter(None, [
            address.street if address else None,
            address.street2 if address else None,
            address_extras.get("colony"),
            address_extras.get("locality") if address_extras.get("locality") != municipality else None,
            municipality or (address.city if address else None),
            f"C.P. {address.zip}" if address and address.zip else None,
        ]))
        address_bottom = ", ".join(filter(None, [
            address.state_id.name if address and address.state_id else None,
            address.country_id.name if address and address.country_id else None,
        ]))
        pdf.setFont("Helvetica", 7)
        for address_line in (address_top, address_bottom):
            for line in simpleSplit(address_line, "Helvetica", 7, company_width):
                pdf.drawCentredString(company_center, y, line)
                y -= 8
        if is_remission and identity and identity.vat:
            pdf.drawCentredString(company_center, y, f"RFC: {identity.vat}")

        right_center = width * 0.805
        right_width = width * 0.30
        report_values = payment._get_payment_receipt_report_values()
        cfdi = report_values.get("cfdi") or {}
        cfdi_node = cfdi.get("cfdi_node")
        stamped = bool(not is_remission and cfdi.get("uuid") and cfdi_node is not None)
        emission = payment.date
        if stamped and cfdi_node.get("Fecha"):
            try:
                emission = date.fromisoformat(cfdi_node.get("Fecha")[:10])
            except ValueError:
                pass
        emission_text = emission.strftime("%d/%m/%Y") if emission else "Sin fecha"

        pdf.setFillColorRGB(0.10, 0.13, 0.18)
        pdf.setFont("Helvetica-Bold", 8.5)
        pdf.drawCentredString(right_center, height - 27, "Folio Fiscal:" if not is_remission else "Remisión interna")
        pdf.setFont("Helvetica", 7.5)
        fiscal_lines = simpleSplit(
            cfdi.get("uuid") if stamped else "No timbrado" if not is_remission else "No es un CFDI",
            "Helvetica", 7.5, right_width,
        )[:2]
        right_y = height - 38
        for line in fiscal_lines:
            pdf.drawCentredString(right_center, right_y, line)
            right_y -= 9
        pdf.setFont("Helvetica", 7.5)
        pdf.drawCentredString(right_center, height - 62,
                              "Tipo de comprobante: P - Pago" if stamped else "Documento interno de pago")
        detail_y = height - 73
        if payment.receipt_sequence:
            pdf.setFont("Helvetica-Bold", 8)
            pdf.drawCentredString(
                right_center, detail_y,
                f"Serie interna: RP  Folio interno: {payment.receipt_sequence:05d}",
            )
            detail_y -= 11
            pdf.setFont("Helvetica", 7.5)
        pdf.drawCentredString(right_center, detail_y,
                              f"Fecha de emisión: {emission_text}")

        pdf.setStrokeColorRGB(0.73, 0.73, 0.73)
        pdf.setLineWidth(0.5)
        # Keep the rule below the entire identity block. With a wide logo,
        # the old right-column-only position could draw through its bottom.
        separator_y = min(logo_bottom, y, detail_y) - 10
        pdf.line(31, separator_y, width - 31, separator_y)
        pdf.save()
        return output.getvalue()
