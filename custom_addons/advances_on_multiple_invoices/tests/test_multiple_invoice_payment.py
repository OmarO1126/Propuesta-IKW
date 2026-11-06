from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from lxml import etree
from PyPDF2 import PdfReader
from reportlab.pdfgen import canvas

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.l10n_mx_edi.tests.common import TestMxEdiCommon as MxEdiCommon
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged


def _create_third_party_payer_with_bank(
    env,
    *,
    suffix="",
    account_number="123456789012345678",
):
    """Create an unrelated payer and an origin account suitable for MX REP tests."""
    bank = env["res.bank"].create({
        "name": f"Third-party payer bank {suffix}".strip(),
        "country": env.ref("base.mx").id,
        "l10n_mx_edi_vat": "BBA830831LJ2",
    })
    payer = env["res.partner"].create({
        "name": f"Third-party payer {suffix}".strip(),
        "is_company": True,
        "country_id": env.ref("base.mx").id,
        "vat": "XAXX010101000",
    })
    payer_bank = env["res.partner.bank"].create({
        "acc_number": account_number,
        "partner_id": payer.id,
        "bank_id": bank.id,
        "company_id": env.company.id,
    })
    return payer, payer_bank


@tagged("post_install", "-at_install")
class TestMultipleInvoicePayment(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.invoice_1 = cls._create_invoice(
            move_type="out_invoice",
            partner_id=cls.partner_a.id,
            post=True,
            invoice_line_ids=[Command.create({
                "name": "Invoice one",
                "price_unit": 120.0,
                "account_id": cls.company_data["default_account_revenue"].id,
                "tax_ids": [Command.clear()],
            })],
        )
        cls.invoice_2 = cls._create_invoice(
            move_type="out_invoice",
            partner_id=cls.partner_a.id,
            post=True,
            invoice_line_ids=[Command.create({
                "name": "Invoice two",
                "price_unit": 80.0,
                "account_id": cls.company_data["default_account_revenue"].id,
                "tax_ids": [Command.clear()],
            })],
        )

    def _open_wizard(self):
        return self.env["multiple.invoice.payment.wizard"].with_context(
            active_model="account.move",
            active_ids=(self.invoice_1 + self.invoice_2).ids,
        ).create({})

    def test_create_one_payment_with_exact_invoice_allocations(self):
        wizard = self._open_wizard()
        wizard.policy_observation = "STD 051026 - revisado"
        wizard.amount = 100.0
        wizard.line_ids.filtered(lambda line: line.invoice_id == self.invoice_1).allocation_amount = 30.0
        wizard.line_ids.filtered(lambda line: line.invoice_id == self.invoice_2).allocation_amount = 70.0

        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])

        self.assertEqual(payment.amount, 100.0)
        self.assertTrue(payment.multiple_invoice_distribution)
        self.assertEqual(payment.policy_observation, "STD 051026 - revisado")
        self.assertTrue(payment.policy_number)
        self.assertEqual(payment.policy_period, payment.date.strftime("%Y-%m"))
        self.assertTrue(payment.is_reconciled)
        self.assertEqual(
            set(payment.reconciled_invoice_ids.ids),
            set((self.invoice_1 + self.invoice_2).ids),
        )

        _liquidity_lines, counterpart_lines, _writeoff_lines = payment._seek_for_lines()
        self.assertEqual(len(counterpart_lines), 2)
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 90.0},
            {"amount_residual": 10.0},
        ])
        self.assertRecordValues(payment.invoice_distribution_ids, [
            {
                "invoice_id": self.invoice_1.id,
                "amount": 30.0,
                "previous_residual": 120.0,
                "remaining_residual": 90.0,
                "reconciliation_active": True,
            },
            {
                "invoice_id": self.invoice_2.id,
                "amount": 70.0,
                "previous_residual": 80.0,
                "remaining_residual": 10.0,
                "reconciliation_active": True,
            },
        ])

    def test_create_three_accounting_payments_from_one_clean_wizard(self):
        wizard = self._open_wizard()
        wizard.write({
            "amount": 40.0,
            "use_second_payment": True,
            "second_amount": 30.0,
            "second_journal_id": wizard.journal_id.id,
            "second_payment_method_line_id": wizard.payment_method_line_id.id,
            "second_memo": "COMP-002",
            "use_third_payment": True,
            "third_amount": 30.0,
            "third_journal_id": wizard.journal_id.id,
            "third_payment_method_line_id": wizard.payment_method_line_id.id,
            "third_memo": "OTHER-003",
        })
        wizard.action_clear_allocations()
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        ).write({
            "allocation_amount": 40.0,
            "second_allocation_amount": 30.0,
        })
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        ).third_allocation_amount = 30.0

        action = wizard.action_create_distributed_payment()
        primary = self.env["account.payment"].browse(action["res_id"])
        components = primary.distributed_group_payment_ids

        self.assertEqual(len(components), 3)
        self.assertFalse(any(components.mapped("receipt_number")))
        self.assertFalse((components - primary).mapped("receipt_sequence")[0])
        self.assertTrue(primary.policy_number)
        self.assertEqual(set(components.mapped("policy_number")), {primary.policy_number})
        self.assertFalse((components - primary).mapped("policy_sequence")[0])
        self.assertEqual(components.mapped("amount"), [40.0, 30.0, 30.0])
        self.assertEqual(primary.distributed_group_total_amount, 100.0)
        self.assertEqual(components.distributed_payment_primary_id, primary)
        self.assertTrue(all(components.mapped("is_reconciled")))
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 50.0},
            {"amount_residual": 50.0},
        ])
        self.assertEqual(
            sum(components.invoice_distribution_ids.mapped("amount")),
            100.0,
        )
        self.assertRecordValues(
            components.sorted("distributed_payment_component_sequence"),
            [
                {"amount": 40.0},
                {"amount": 30.0},
                {"amount": 30.0},
            ],
        )

    def test_policy_sequence_is_company_wide_and_restarts_each_month(self):
        journal = self.company_data["default_journal_bank"]
        method = journal._get_available_payment_method_lines("inbound")[:1]

        def create_payment(payment_date):
            payment = self.env["account.payment"].create({
                "amount": 10.0,
                "date": payment_date,
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": self.partner_a.id,
                "journal_id": journal.id,
                "company_id": self.env.company.id,
                "currency_id": self.env.company.currency_id.id,
                "payment_method_line_id": method.id,
            })
            payment.action_post()
            return payment

        first = create_payment(fields.Date.from_string("2099-02-05"))
        second = create_payment(fields.Date.from_string("2099-02-06"))
        next_month = create_payment(fields.Date.from_string("2099-03-01"))
        self.assertEqual((first.policy_number, second.policy_number), ("00001", "00002"))
        self.assertEqual(next_month.policy_number, "00001")
        self.assertEqual((first.policy_period, next_month.policy_period), ("2099-02", "2099-03"))
        second.action_cancel()
        second.action_draft()
        second.action_post()
        self.assertEqual(second.policy_number, "00002")
        first.action_cancel()
        first.action_draft()
        with self.env.cr.savepoint(), self.assertRaisesRegex(ValidationError, "póliza"):
            first.date = fields.Date.from_string("2099-03-05")

    def test_internal_receipt_series_ignores_payments_without_signed_ppd_rep(self):
        first_journal = self.company_data["default_journal_bank"]
        other_journal = first_journal.copy({
            "name": "Other bank for RP test",
            "code": "RPT",
        })

        def create_payment(journal):
            method = journal._get_available_payment_method_lines("inbound")[:1]
            payment = self.env["account.payment"].create({
                "amount": 10.0,
                "date": fields.Date.from_string("2099-04-05"),
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": self.partner_a.id,
                "journal_id": journal.id,
                "company_id": self.env.company.id,
                "currency_id": self.env.company.currency_id.id,
                "payment_method_line_id": method.id,
            })
            payment.action_post()
            return payment

        anchor = create_payment(first_journal)
        other_bank = create_payment(other_journal)
        self.assertFalse(anchor.receipt_number)
        self.assertFalse(other_bank.receipt_number)
        with self.assertRaisesRegex(UserError, "timbra el complemento"):
            anchor._start_receipt_series()
        self.assertFalse(create_payment(first_journal).receipt_number)
        other_bank.action_cancel()
        other_bank.action_draft()
        other_bank.action_post()
        self.assertFalse(other_bank.receipt_number)

    def test_receipt_series_cannot_start_from_another_payment(self):
        payment = self.env["account.payment"].create({
            "amount": 10.0,
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.partner_a.id,
            "journal_id": self.company_data["default_journal_bank"].id,
            "company_id": self.env.company.id,
            "currency_id": self.env.company.currency_id.id,
            "payment_method_line_id": self.company_data["default_journal_bank"]._get_available_payment_method_lines("inbound")[:1].id,
        })
        payment.action_post()
        with self.assertRaisesRegex(UserError, "PBC/2026/00014"):
            payment.action_start_receipt_series()

    def test_observation_suggestion_uses_journal_code_and_day_month_year(self):
        wizard = self._open_wizard()
        payment_date = fields.Date.from_string("2026-10-05")
        for prefix in ("STD", "BNK2", "BNK3"):
            self.assertEqual(
                wizard._suggest_policy_observation(
                    SimpleNamespace(code=prefix), payment_date,
                ),
                "%s 051026" % prefix,
            )

    def test_policy_report_is_available_from_posted_payment(self):
        wizard = self._open_wizard()
        wizard.amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        ).allocation_amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        ).allocation_amount = 0.0
        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        report_action = payment.action_print_policy()
        self.assertIn(report_action["type"], ("ir.actions.report", "ir.actions.act_window"))
        report = self.env.ref("advances_on_multiple_invoices.action_report_payment_policy")
        self.assertEqual(
            report.report_name,
            "advances_on_multiple_invoices.report_payment_policy",
        )
        self.assertEqual(report.report_type, "qweb-pdf")
        self.env.company.partner_id.street = "NO IMPRIMIR DOMICILIO"
        html, _report_type = report._render_qweb_html(
            report.report_name, payment.ids,
        )
        self.assertIn(payment.policy_number.encode(), html)
        self.assertIn(b"Impresi", html)
        self.assertIn(self.env.company.partner_id.name.encode(), html)
        self.assertIn(b"Empresa / sucursal", html)
        self.assertNotIn(b"NO IMPRIMIR DOMICILIO", html)
        _bodies, _ids, _header, _footer, paperformat_args = report._prepare_html(
            html, report_model="account.payment",
        )
        self.assertEqual(paperformat_args["data-report-margin-top"], "35")
        self.assertEqual(paperformat_args["data-report-header-spacing"], "35")

    def test_receipt_shows_only_payer_and_compact_document_names(self):
        partner_values = {
            "ref": "0009",
            "vat": "XAXX010101000",
            "country_id": self.env.ref("base.mx").id,
            "l10n_mx_edi_fiscal_regime": "601",
            "street": "Calle Pagador 123",
            "street2": "Interior 2",
            "city": "Monterrey",
            "zip": "64000",
            "phone": "8181234567",
            "email": "pagador@example.test",
        }
        if "l10n_mx_edi_colony" in self.partner_a._fields:
            partner_values["l10n_mx_edi_colony"] = "Colonia Centro"
        self.partner_a.write(partner_values)
        company_address = self.env.company.partner_id
        company_address.write({
            "street": "Avenida Empresa 456",
            "street2": "Interior 3",
            "city": "Monterrey",
            "zip": "64550",
            "country_id": self.env.ref("base.mx").id,
        })
        company_state = self.env["res.country.state"].search([
            ("country_id", "=", self.env.ref("base.mx").id),
            ("code", "=", "NL"),
        ], limit=1)
        if company_state:
            company_address.state_id = company_state
        if "l10n_mx_edi_colony" in company_address._fields:
            company_address.l10n_mx_edi_colony = "Colonia Empresa"
        self.invoice_1.invoice_origin = "S00099"
        wizard = self._open_wizard()
        wizard.amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        ).allocation_amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        ).allocation_amount = 0.0
        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        payment.receipt_sequence = 1  # Synthetic folio to exercise the report layout.
        report = self.env.ref("account.action_report_payment_receipt")
        html, _report_type = report._render_qweb_html(
            report.report_name, payment.ids,
        )
        payment_methods = etree.HTML(html).xpath(
            "//div[@name='multiple_invoice_payment_methods']"
        )
        self.assertEqual(len(payment_methods), 1)
        method_text = " ".join(payment_methods[0].itertext())
        self.assertIn("Banco ordenante:", method_text)
        self.assertIn("No informado en XML", method_text)
        self.assertNotIn("Diario:", method_text)
        method_card = payment_methods[0].xpath("./div[contains(@class, 'mb-3')]")[0]
        self.assertNotIn("page-break-inside: avoid", method_card.get("style", ""))
        identity = method_card.xpath("./div[@name='multiple_invoice_method_identity']")
        self.assertEqual(len(identity), 1)
        self.assertIn("page-break-inside: avoid", identity[0].get("style", ""))
        distribution_table = method_card.xpath("./table[contains(@class, 'table-sm')]")[0]
        self.assertIn("table-header-group", distribution_table.xpath("./thead")[0].get("style", ""))
        for row in distribution_table.xpath("./tbody/tr"):
            self.assertIn("page-break-inside: avoid", row.get("style", ""))
        summary = etree.HTML(html).xpath("//div[@name='multiple_invoice_payment_summary']")
        self.assertEqual(len(summary), 1)
        self.assertIn("font-size: 12px", summary[0].get("style", ""))
        self.assertFalse(summary[0].xpath(".//table"))
        self.assertEqual(summary[0].xpath(".//span[@name='payer_cfdi_usage']")[0].text, None)
        payer_text = " ".join(summary[0].itertext())
        for value in (
            "Datos pagador", "Cliente:", "0009", "R.F.C.:",
            "XAXX010101000", "Uso de CFDI:",
            "Régimen fiscal:", "601", "Razón social:",
            "Calle Pagador 123", "Interior 2", "Municipio:",
            "Monterrey", "C.P.:", "64000",
        ):
            self.assertIn(value, payer_text)
        self.assertNotIn("Pendiente de timbrado", payer_text)
        self.assertNotIn("Colonia:", payer_text)
        self.assertNotIn("Colonia Centro", payer_text)
        self.assertNotIn("8181234567", payer_text)
        self.assertNotIn("pagador@example.test", payer_text)
        self.assertNotIn("Importe de pago", payer_text)
        self.assertNotIn("Núm. de operación", payer_text)

        class StudioAddress:
            city = "Ciudad de respaldo"
            _fields = {
                "x_studio_colonia_cliente": SimpleNamespace(type="char", string="Colonia"),
                "x_studio_municipio_cliente": SimpleNamespace(type="char", string="Municipio"),
                "x_studio_codigo_colonia": SimpleNamespace(type="char", string="Código colonia"),
            }

            def __getitem__(self, name):
                return {
                    "x_studio_colonia_cliente": "Colonia Studio",
                    "x_studio_municipio_cliente": "Municipio Studio",
                    "x_studio_codigo_colonia": "9999",
                }[name]

        self.assertEqual(
            payment._get_receipt_payer_address_extras(StudioAddress()),
            {"colony": "Colonia Studio", "locality": False, "municipality": "Municipio Studio"},
        )
        self.assertIn(b"Datos pagador", html)
        self.assertIn(b"Calle Pagador 123", html)
        self.assertNotIn(b"8181234567", html)
        self.assertNotIn(b"pagador@example.test", html)
        company_header = etree.HTML(html).xpath("//div[@name='multiple_invoice_company_header']")
        self.assertEqual(len(company_header), 1)
        company_text = " ".join(company_header[0].itertext())
        for value in (
            "Avenida Empresa 456", "Interior 3", "Monterrey", "64550",
            company_address.country_id.name,
        ):
            self.assertIn(value, company_text)
        if company_state:
            self.assertIn(company_state.name, company_text)
        if "l10n_mx_edi_colony" in company_address._fields:
            self.assertIn("Colonia Empresa", company_text)
        self.assertIn(b"Folio Fiscal", html)
        self.assertIn(b"Serie interna:", html)
        self.assertIn(b"Folio interno:", html)
        self.assertIn(b"00001", html)
        self.assertIn(b"No timbrado", html)
        self.assertNotIn(b"Fecha de vencimiento", html)
        self.assertIn(self.invoice_1.name.encode(), html)
        self.assertIn(b"(S00099)", html)
        self.assertNotIn(b"Cliente / receptor del CFDI", html)
        self.assertNotIn(b"<strong>Tipo:</strong>", html)
        self.assertNotIn(b"<strong>Emisi", html)
        self.assertNotIn(b"<strong>Vencimiento:</strong>", html)

    def test_distributed_receipt_identity_is_repeated_on_each_pdf_page(self):
        self.env.company.partner_id.write({
            "street": "Avenida Empresa 456",
            "street2": "Interior 3",
            "city": "Monterrey",
            "zip": "64550",
            "phone": "8100000000",
            "country_id": self.env.ref("base.mx").id,
        })
        wizard = self._open_wizard()
        wizard.amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        ).allocation_amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        ).allocation_amount = 0.0
        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        payment.receipt_sequence = 1  # Synthetic folio to exercise the report layout.

        receipt = self.env.ref("account.action_report_payment_receipt")
        html, _report_type = receipt._render_qweb_html(
            receipt.report_name, payment.ids,
        )
        bodies, _ids, header, _footer, paperformat_args = receipt.with_context(
            compact_receipt_pdf_overlay=True
        )._prepare_html(html, report_model="account.payment")
        self.assertIsNone(header)
        self.assertNotIn("Recibo de pago:", bodies[0])
        self.assertEqual(paperformat_args["data-report-margin-top"], "44")

        source = BytesIO()
        pdf = canvas.Canvas(source, pagesize=(595, 842))
        for _index in range(2):
            pdf.drawString(40, 500, "Payment content")
            pdf.showPage()
        pdf.save()

        decorated = receipt._add_distributed_receipt_identity(source.getvalue(), payment)
        pages = PdfReader(BytesIO(decorated)).pages
        self.assertEqual(len(pages), 2)
        for page in pages:
            self.assertIn("Payment content", page.extract_text())
            self.assertIn(self.env.company.name, page.extract_text())
            self.assertIn("Serie interna: RP", page.extract_text())
            self.assertIn("Folio interno: 00001", page.extract_text())
            self.assertIn("Fecha de emisión:", page.extract_text())
            self.assertIn("Avenida Empresa 456", page.extract_text())
            self.assertIn("Interior 3", page.extract_text())
            self.assertIn("C.P. 64550", page.extract_text())
            self.assertIn(
                self.env.company.partner_id.country_id.name,
                page.extract_text(),
            )
            self.assertNotIn("8100000000", page.extract_text())
            self.assertNotIn("Fecha de vencimiento:", page.extract_text())

    def test_stamped_receipt_keeps_cfdi_series_separate_from_internal_rp(self):
        wizard = self._open_wizard()
        wizard.amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        ).allocation_amount = 15.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        ).allocation_amount = 0.0
        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        payment.receipt_sequence = 1  # Synthetic folio to exercise the report layout.
        cfdi_node = etree.Element(
            "Comprobante", Serie="CF", Folio="0098", Fecha="2026-10-05T12:00:00",
        )
        cfdi_values = {
            "cfdi": {
                "uuid": "11111111-2222-3333-4444-555555555555",
                "cfdi_node": cfdi_node,
            },
        }
        receipt = self.env.ref("account.action_report_payment_receipt")
        with patch.object(
            type(payment), "_get_payment_receipt_report_values",
            return_value=cfdi_values,
        ):
            overlay = receipt._make_distributed_receipt_identity_overlay(
                payment, (595, 842),
            )
        text = PdfReader(BytesIO(overlay)).pages[0].extract_text()
        self.assertNotIn("Serie CFDI:", text)
        self.assertNotIn("Folio CFDI:", text)
        self.assertIn("Serie interna: RP  Folio interno: 00001", text)
        payment.receipt_sequence = 0
        with patch.object(
            type(payment), "_get_payment_receipt_report_values",
            return_value=cfdi_values,
        ):
            overlay_without_rp = receipt._make_distributed_receipt_identity_overlay(
                payment, (595, 842),
            )
        self.assertNotIn(
            "Serie interna:",
            PdfReader(BytesIO(overlay_without_rp)).pages[0].extract_text(),
        )

    def test_canceling_any_component_cancels_the_complete_payment_group(self):
        wizard = self._open_wizard()
        wizard.write({
            "amount": 60.0,
            "use_second_payment": True,
            "second_amount": 40.0,
            "second_journal_id": wizard.journal_id.id,
            "second_payment_method_line_id": wizard.payment_method_line_id.id,
        })
        wizard.action_clear_allocations()
        first_line = wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        )
        second_line = wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        )
        first_line.allocation_amount = 60.0
        second_line.second_allocation_amount = 40.0

        action = wizard.action_create_distributed_payment()
        primary = self.env["account.payment"].browse(action["res_id"])
        components = primary.distributed_group_payment_ids
        satellite = components - primary
        self.assertEqual(len(components), 2)
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 60.0},
            {"amount_residual": 40.0},
        ])

        satellite.action_cancel()

        self.assertEqual(set(components.mapped("state")), {"canceled"})
        self.assertEqual(set(components.move_id.mapped("state")), {"cancel"})
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 120.0},
            {"amount_residual": 80.0},
        ])
        self.assertFalse(any(
            components.invoice_distribution_ids.mapped("reconciliation_active")
        ))

        primary.action_draft()
        self.assertEqual(set(components.mapped("state")), {"draft"})
        self.assertEqual(set(components.move_id.mapped("state")), {"draft"})

        satellite.action_post()
        self.assertEqual(set(components.move_id.mapped("state")), {"posted"})
        self.assertTrue(all(components.mapped("is_reconciled")))
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 60.0},
            {"amount_residual": 40.0},
        ])
        self.assertTrue(all(
            components.invoice_distribution_ids.mapped("reconciliation_active")
        ))

    def test_canceling_primary_move_cancels_all_payment_components(self):
        wizard = self._open_wizard()
        wizard.write({
            "amount": 60.0,
            "use_second_payment": True,
            "second_amount": 40.0,
            "second_journal_id": wizard.journal_id.id,
            "second_payment_method_line_id": wizard.payment_method_line_id.id,
        })
        wizard.action_clear_allocations()
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        ).allocation_amount = 60.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        ).second_allocation_amount = 40.0

        action = wizard.action_create_distributed_payment()
        primary = self.env["account.payment"].browse(action["res_id"])
        components = primary.distributed_group_payment_ids

        primary.move_id.button_cancel()

        self.assertEqual(set(components.mapped("state")), {"canceled"})
        self.assertEqual(set(components.move_id.mapped("state")), {"cancel"})
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 120.0},
            {"amount_residual": 80.0},
        ])

    def test_payer_defaults_to_invoice_customer(self):
        wizard = self._open_wizard()

        self.assertEqual(wizard.partner_id, self.partner_a.commercial_partner_id)
        self.assertEqual(wizard.payer_id, self.partner_a.commercial_partner_id)

    def test_third_party_payer_settles_customer_debt_and_prints_its_account(self):
        payer, payer_bank = _create_third_party_payer_with_bank(
            self.env,
            suffix="accounting",
        )
        wizard = self._open_wizard()
        wizard.write({
            "payer_id": payer.id,
            "payer_bank_id": payer_bank.id,
        })

        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])

        self.assertEqual(payment.partner_id, self.partner_a.commercial_partner_id)
        self.assertEqual(payment.distributed_payer_id, payer.commercial_partner_id)
        self.assertEqual(payment.distributed_payer_bank_id, payer_bank)
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 0.0},
            {"amount_residual": 0.0},
        ])
        receivable_lines = payment.move_id.line_ids.filtered(
            lambda line: line.account_type == "asset_receivable"
        )
        self.assertTrue(receivable_lines)
        self.assertEqual(
            receivable_lines.partner_id,
            self.partner_a.commercial_partner_id,
        )

        report_values = payment._get_multiple_invoice_payment_bank_report_values({})
        self.assertEqual(report_values["emitter_name"], payer.display_name)
        self.assertEqual(report_values["emitter_account"], payer_bank.acc_number)
        self.assertEqual(report_values["emitter_bank_name"], payer_bank.bank_id.name)

    def test_single_contact_bank_is_suggested_but_remains_selectable(self):
        bank = self.env["res.partner.bank"].create({
            "acc_number": "012345678901234567",
            "partner_id": self.partner_a.id,
        })
        wizard = self._open_wizard()
        self.assertEqual(wizard.payer_bank_id, bank)

        other_bank = self.env["res.partner.bank"].create({
            "acc_number": "987654321098765432",
            "partner_id": self.partner_a.id,
        })
        wizard.payer_bank_id = other_bank
        wizard._onchange_payer_id()
        self.assertEqual(wizard.payer_bank_id, other_bank)

    def test_multiple_contact_banks_require_explicit_selection(self):
        self.env["res.partner.bank"].create({
            "acc_number": "012345678901234567",
            "partner_id": self.partner_a.id,
        })
        self.env["res.partner.bank"].create({
            "acc_number": "987654321098765432",
            "partner_id": self.partner_a.id,
        })
        wizard = self._open_wizard()
        self.assertFalse(wizard.payer_bank_id)

    def test_changing_payer_suggests_its_only_bank(self):
        payer, payer_bank = _create_third_party_payer_with_bank(
            self.env, suffix="suggestion",
        )
        wizard = self._open_wizard()
        wizard.payer_id = payer
        wizard._onchange_payer_id()
        self.assertEqual(wizard.payer_bank_id, payer_bank)

    def test_payer_bank_must_belong_to_selected_payer(self):
        payer, _payer_bank = _create_third_party_payer_with_bank(
            self.env,
            suffix="selected",
        )
        _other_payer, other_bank = _create_third_party_payer_with_bank(
            self.env,
            suffix="unrelated",
            account_number="987654321098765432",
        )
        wizard = self._open_wizard()

        with self.assertRaises(ValidationError):
            wizard.write({
                "payer_id": payer.id,
                "payer_bank_id": other_bank.id,
            })
            wizard.action_create_distributed_payment()

    def test_payment_must_be_fully_allocated(self):
        wizard = self._open_wizard()
        wizard.amount = 100.0
        wizard.line_ids.filtered(lambda line: line.invoice_id == self.invoice_1).allocation_amount = 60.0
        wizard.line_ids.filtered(lambda line: line.invoice_id == self.invoice_2).allocation_amount = 20.0

        with self.assertRaisesRegex(ValidationError, "complete payment must be allocated"):
            wizard.action_create_distributed_payment()

    def test_oldest_first_never_exceeds_available_balance(self):
        wizard = self._open_wizard()
        wizard.amount = 150.0

        wizard.action_allocate_oldest()

        self.assertEqual(sum(wizard.line_ids.mapped("allocation_amount")), 150.0)
        for line in wizard.line_ids:
            self.assertLessEqual(line.allocation_amount, line.current_residual)

    def test_second_method_distribution_shows_and_respects_first_method(self):
        wizard = self._open_wizard()
        wizard.write({
            "amount": 60.0,
            "use_second_payment": True,
            "second_amount": 40.0,
            "second_journal_id": wizard.journal_id.id,
            "second_payment_method_line_id": wizard.payment_method_line_id.id,
        })
        wizard.action_clear_allocations()
        ordered_invoices = wizard._sort_invoices(wizard.line_ids.invoice_id)
        first_line = wizard.line_ids.filtered(
            lambda line: line.invoice_id == ordered_invoices[0]
        )

        wizard.action_allocate_oldest()
        self.assertEqual(first_line.allocation_amount, 60.0)
        self.assertEqual(first_line.remaining_after_first, 60.0)

        wizard.action_allocate_second_oldest()
        self.assertEqual(first_line.second_allocation_amount, 40.0)
        self.assertEqual(first_line.total_allocation_amount, 100.0)
        self.assertEqual(first_line.remaining_after_second, 20.0)
        self.assertEqual(wizard.first_amount_to_allocate, 0.0)
        self.assertEqual(wizard.second_amount_to_allocate, 0.0)

    def test_adding_payment_methods_does_not_duplicate_invoice_lines(self):
        wizard = self._open_wizard()
        original_line_ids = wizard.line_ids.ids
        original_invoice_ids = wizard.line_ids.invoice_id.ids

        wizard.action_add_second_payment()
        wizard.action_add_third_payment()

        self.assertEqual(wizard.line_ids.ids, original_line_ids)
        self.assertEqual(wizard.line_ids.invoice_id.ids, original_invoice_ids)
        self.assertEqual(len(wizard.line_ids), len(set(wizard.line_ids.invoice_id.ids)))

    def test_invoice_filter_preserves_allocations(self):
        wizard = self._open_wizard()
        first_line = wizard.line_ids.filtered(lambda line: line.invoice_id == self.invoice_1)
        second_line = wizard.line_ids.filtered(lambda line: line.invoice_id == self.invoice_2)
        first_line.allocation_amount = 25.0
        second_line.allocation_amount = 15.0

        self.assertEqual(
            set(wizard.available_invoice_ids.ids),
            set((self.invoice_1 + self.invoice_2).ids),
        )
        wizard.invoice_filter_ids = self.invoice_1
        action = wizard.action_apply_invoice_filter()

        self.assertTrue(wizard.invoice_filter_applied)
        self.assertEqual(
            wizard.filtered_line_ids,
            first_line,
        )
        self.assertEqual(action["res_id"], wizard.id)
        wizard.filtered_line_ids.allocation_amount = 35.0
        self.assertEqual(first_line.allocation_amount, 35.0)

        action = wizard.action_clear_invoice_filter()

        self.assertFalse(wizard.invoice_filter_ids)
        self.assertFalse(wizard.invoice_filter_applied)
        self.assertFalse(wizard.filtered_line_ids)
        self.assertEqual(first_line.allocation_amount, 35.0)
        self.assertEqual(second_line.allocation_amount, 15.0)
        self.assertEqual(action["res_id"], wizard.id)

    def test_invoice_filter_searches_invoice_reference(self):
        wizard = self._open_wizard()
        self.invoice_1.ref = "SEARCH-REFERENCE-001"

        matches = self.env["account.move"].name_search(
            "SEARCH-REFERENCE-001",
            domain=[("id", "in", wizard.available_invoice_ids.ids)],
        )

        self.assertIn(self.invoice_1.id, dict(matches))

    def test_oldest_and_newest_respect_the_selected_documents(self):
        wizard = self._open_wizard()
        wizard.amount = 50.0
        wizard.action_clear_allocations()
        ordered_invoices = wizard._sort_invoices(wizard.line_ids.invoice_id)
        first_line = wizard.line_ids.filtered(
            lambda line: line.invoice_id == ordered_invoices[0]
        )
        last_line = wizard.line_ids.filtered(
            lambda line: line.invoice_id == ordered_invoices[-1]
        )
        wizard.invoice_filter_ids = ordered_invoices[0]
        wizard.action_apply_invoice_filter()

        wizard.action_allocate_oldest()

        self.assertTrue(wizard.invoice_filter_applied)
        self.assertEqual(first_line.allocation_amount, 50.0)
        self.assertEqual(last_line.allocation_amount, 0.0)

        wizard.action_clear_invoice_filter()
        wizard.action_allocate_newest()

        self.assertEqual(first_line.allocation_amount, 0.0)
        self.assertEqual(last_line.allocation_amount, 50.0)

    def test_multi_invoice_filter_keeps_other_allocations_and_allows_scoped_distribution(self):
        extra_invoices = self.env["account.move"]
        for number in (3, 4):
            extra_invoices += self._create_invoice(
                move_type="out_invoice",
                partner_id=self.partner_a.id,
                post=True,
                invoice_line_ids=[Command.create({
                    "name": f"Invoice {number}",
                    "price_unit": 40.0,
                    "account_id": self.company_data["default_account_revenue"].id,
                    "tax_ids": [Command.clear()],
                })],
            )
        invoices = self.invoice_1 + self.invoice_2 + extra_invoices
        wizard = self.env["multiple.invoice.payment.wizard"].with_context(
            active_model="account.move", active_ids=invoices.ids,
        ).create({})
        wizard.amount = 50.0
        wizard.action_clear_allocations()
        hidden_line = wizard.line_ids.filtered(
            lambda line: line.invoice_id == extra_invoices[1]
        )
        hidden_line.allocation_amount = 10.0
        selected = self.invoice_1 + self.invoice_2 + extra_invoices[0]
        wizard.invoice_filter_ids = selected

        wizard.action_apply_invoice_filter()

        self.assertEqual(set(wizard.filtered_line_ids.invoice_id.ids), set(selected.ids))
        self.assertEqual(wizard.filtered_second_line_ids, wizard.filtered_line_ids)
        self.assertEqual(wizard.filtered_third_line_ids, wizard.filtered_line_ids)

        wizard.action_allocate_oldest()

        self.assertTrue(wizard.invoice_filter_applied)
        self.assertEqual(hidden_line.allocation_amount, 10.0)
        self.assertEqual(sum(wizard.filtered_line_ids.mapped("allocation_amount")), 40.0)
        self.assertEqual(wizard.amount_to_allocate, 0.0)

        wizard.action_clear_first_allocations()

        self.assertEqual(hidden_line.allocation_amount, 10.0)
        self.assertFalse(any(wizard.filtered_line_ids.mapped("allocation_amount")))

        wizard.action_allocate_proportionally()

        self.assertEqual(hidden_line.allocation_amount, 10.0)
        self.assertEqual(sum(wizard.filtered_line_ids.mapped("allocation_amount")), 40.0)

    def test_payment_amount_above_total_outstanding_is_flagged(self):
        wizard = self._open_wizard()
        total_outstanding = sum(wizard.line_ids.mapped("current_residual"))

        wizard.amount = total_outstanding + 1.0
        wizard.action_allocate_oldest()

        self.assertEqual(wizard.total_outstanding_amount, total_outstanding)
        self.assertTrue(wizard.amount_exceeds_outstanding)
        self.assertTrue(wizard.show_payment_difference)
        self.assertEqual(wizard.payment_difference, -1.0)
        self.assertEqual(wizard.excess_amount, 1.0)
        self.assertEqual(wizard.amount_to_allocate, 0.0)
        self.assertTrue(wizard.allocation_complete)

    def test_overpayment_can_remain_as_open_customer_credit(self):
        wizard = self._open_wizard()
        wizard.amount = 210.0
        wizard.payment_difference_handling = "open"
        wizard.action_allocate_oldest()

        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])

        self.assertEqual(payment.amount, 210.0)
        self.assertEqual(payment.distributed_excess_amount, 10.0)
        self.assertEqual(payment.distributed_difference_handling, "open")
        self.assertFalse(payment.distributed_writeoff_account_id)
        self.assertFalse(payment.is_reconciled)
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 0.0},
            {"amount_residual": 0.0},
        ])

        _liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
        self.assertFalse(writeoff_lines)
        self.assertEqual(len(counterpart_lines), 3)
        open_credit_lines = counterpart_lines.filtered(lambda line: not line.reconciled)
        self.assertEqual(len(open_credit_lines), 1)
        self.assertEqual(open_credit_lines.amount_currency, -10.0)
        self.assertEqual(open_credit_lines.account_id.account_type, "asset_receivable")
        self.assertEqual(open_credit_lines.partner_id, self.partner_a.commercial_partner_id)

    def test_small_unapplied_amount_can_be_posted_while_invoices_remain_open(self):
        wizard = self._open_wizard()
        difference_account = self.company_data["default_account_expense"]
        wizard.action_clear_allocations()
        wizard.write({
            "amount": 100.12,
            "allow_unallocated_amount": True,
            "payment_difference_handling": "reconcile",
            "writeoff_account_id": difference_account.id,
            "writeoff_label": "Diferencia por centavos",
        })
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_1
        ).allocation_amount = 60.0
        wizard.line_ids.filtered(
            lambda line: line.invoice_id == self.invoice_2
        ).allocation_amount = 40.0

        self.assertTrue(wizard.allocation_complete)
        self.assertEqual(wizard.excess_amount, 0.12)
        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])

        self.assertEqual(payment.amount, 100.12)
        self.assertEqual(payment.distributed_excess_amount, 0.12)
        self.assertEqual(payment.distributed_writeoff_account_id, difference_account)
        self.assertTrue(payment.is_reconciled)
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 60.0},
            {"amount_residual": 40.0},
        ])
        _liquidity_lines, _counterpart_lines, writeoff_lines = payment._seek_for_lines()
        self.assertEqual(len(writeoff_lines), 1)
        self.assertEqual(writeoff_lines.amount_currency, -0.12)

    def test_forgiven_centavo_is_expense_and_not_part_of_cash_payment(self):
        wizard = self._open_wizard()
        wizard.action_clear_allocations()
        expense = self.company_data["default_account_expense"]
        journal = self.env["account.journal"].search([
            ("type", "=", "general"),
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        if not journal:
            journal = self.env["account.journal"].create({
                "name": "Centavo differences",
                "code": "CNTV",
                "type": "general",
                "company_id": self.env.company.id,
            })
        wizard.write({
            "amount": 119.99,
            "shortfall_account_id": expense.id,
            "shortfall_journal_id": journal.id,
        })
        line = wizard.line_ids.filtered(lambda item: item.invoice_id == self.invoice_1)
        line.allocation_amount = 119.99
        line.forgive_shortfall = True

        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        distribution = payment.invoice_distribution_ids
        adjustment = distribution.shortfall_move_id

        self.assertEqual(payment.amount, 119.99)
        self.assertEqual(distribution.amount, 119.99)
        self.assertAlmostEqual(distribution.remaining_residual, 0.01, places=2)
        self.assertAlmostEqual(distribution.shortfall_amount, 0.01, places=2)
        self.assertEqual(self.invoice_1.amount_residual, 0.0)
        self.assertEqual(self.invoice_2.amount_residual, 80.0)
        self.assertEqual(adjustment.state, "posted")
        self.assertAlmostEqual(adjustment.line_ids.filtered(lambda item: item.account_id == expense).debit, 0.01, places=2)
        self.assertAlmostEqual(adjustment.line_ids.filtered(
            lambda item: item.account_id.account_type == "asset_receivable"
        ).credit, 0.01, places=2)
        self.assertEqual(sum(payment.move_id.line_ids.mapped("debit")), 119.99)

        payment.action_cancel()
        self.assertEqual(adjustment.state, "cancel")
        self.assertFalse(distribution.shortfall_move_id)
        self.assertEqual(self.invoice_1.amount_residual, 120.0)
        payment.action_draft()
        payment.action_post()
        self.assertEqual(self.invoice_1.amount_residual, 0.0)
        self.assertTrue(distribution.shortfall_move_id)
        self.assertNotEqual(distribution.shortfall_move_id, adjustment)

    def test_forgiven_shortfall_rejects_whole_currency_unit(self):
        wizard = self._open_wizard()
        wizard.action_clear_allocations()
        wizard.write({
            "amount": 119.0,
            "shortfall_account_id": self.company_data["default_account_expense"].id,
        })
        line = wizard.line_ids.filtered(lambda item: item.invoice_id == self.invoice_1)
        line.allocation_amount = 119.0
        line.forgive_shortfall = True

        with self.assertRaisesRegex(ValidationError, "below 1.00"):
            wizard.action_create_distributed_payment()

    def test_foreign_currency_overpayment_writeoff_keeps_payment_balanced(self):
        payment_date = fields.Date.from_string("2026-01-15")
        foreign_currency = self.env["res.currency"].create({
            "name": "R07",
            "symbol": "R7",
            "rounding": 0.01,
            "active": True,
        })
        self.env["res.currency.rate"].create({
            "name": payment_date,
            "rate": 7.0,
            "currency_id": foreign_currency.id,
            "company_id": self.env.company.id,
        })
        invoices = self.env["account.move"]
        for index, invoice_amount in enumerate((50.0, 49.0), start=1):
            invoices |= self._create_invoice(
                move_type="out_invoice",
                partner_id=self.partner_a.id,
                currency_id=foreign_currency.id,
                invoice_date=payment_date,
                date=payment_date,
                post=True,
                invoice_line_ids=[Command.create({
                    "name": f"Rate seven invoice {index}",
                    "price_unit": invoice_amount,
                    "account_id": self.company_data["default_account_revenue"].id,
                    "tax_ids": [Command.clear()],
                })],
            )

        wizard = self.env["multiple.invoice.payment.wizard"].with_context(
            active_model="account.move",
            active_ids=invoices.ids,
        ).create({
            "payment_date": payment_date,
            "amount": 100.0,
            "payment_difference_handling": "reconcile",
            "writeoff_account_id": self.company_data["default_account_expense"].id,
            "writeoff_label": "Rate seven excess",
        })
        wizard.action_allocate_oldest()

        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()

        self.assertEqual(len(liquidity_lines), 1)
        self.assertEqual(liquidity_lines.balance, 14.29)
        self.assertEqual(liquidity_lines.amount_currency, 100.0)
        self.assertEqual(len(counterpart_lines), 2)
        self.assertAlmostEqual(sum(counterpart_lines.mapped("balance")), -14.15, places=2)
        self.assertEqual(sum(counterpart_lines.mapped("amount_currency")), -99.0)
        self.assertEqual(len(writeoff_lines), 1)
        self.assertEqual(writeoff_lines.balance, -0.14)
        self.assertEqual(writeoff_lines.amount_currency, -1.0)
        self.assertEqual(
            payment.move_id.company_currency_id.round(
                sum(payment.move_id.line_ids.mapped("balance"))
            ),
            0.0,
        )
        self.assertRecordValues(invoices, [
            {"amount_residual": 0.0},
            {"amount_residual": 0.0},
        ])

    def test_overpayment_can_be_posted_to_a_difference_account(self):
        wizard = self._open_wizard()
        difference_account = self.company_data["default_account_expense"]
        wizard.write({
            "amount": 210.0,
            "payment_difference_handling": "reconcile",
            "writeoff_account_id": difference_account.id,
            "writeoff_label": "Customer advance",
        })
        wizard.action_allocate_oldest()

        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])

        self.assertEqual(payment.distributed_excess_amount, 10.0)
        self.assertEqual(payment.distributed_difference_handling, "reconcile")
        self.assertEqual(payment.distributed_writeoff_account_id, difference_account)
        self.assertEqual(payment.distributed_writeoff_label, "Customer advance")
        self.assertTrue(payment.is_reconciled)
        self.assertRecordValues(self.invoice_1 + self.invoice_2, [
            {"amount_residual": 0.0},
            {"amount_residual": 0.0},
        ])

        _liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
        self.assertEqual(len(counterpart_lines), 2)
        self.assertEqual(len(writeoff_lines), 1)
        self.assertEqual(writeoff_lines.account_id, difference_account)
        self.assertEqual(writeoff_lines.name, "Customer advance")
        self.assertEqual(writeoff_lines.amount_currency, -10.0)

    def test_overpayment_difference_account_is_required(self):
        wizard = self._open_wizard()
        wizard.amount = 210.0
        wizard.payment_difference_handling = "reconcile"
        wizard.action_allocate_oldest()

        with self.assertRaisesRegex(
            ValidationError,
            "Select the account that will receive the unapplied amount",
        ):
            wizard.action_create_distributed_payment()

    def test_bank_report_values_prioritize_stamped_cfdi(self):
        wizard = self._open_wizard()
        wizard.payer_bank_id = False  # Exercise an older payment without a stored bank.
        wizard.amount = 100.0
        wizard.line_ids[0].allocation_amount = 100.0
        wizard.line_ids[1].allocation_amount = 0.0
        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])

        values = payment._get_multiple_invoice_payment_bank_report_values({
            "payment_info": {
                "from_account_number": "0123456789",
                "from_account_name": "Emitter Bank",
                "to_account_number": "9876543210",
            },
        })

        self.assertEqual(values["emitter_account"], "0123456789")
        self.assertEqual(values["emitter_bank_name"], "Emitter Bank")
        self.assertEqual(values["receiver_account"], "9876543210")

    def test_receipt_reads_signed_ordering_account_when_payment_field_is_empty(self):
        wizard = self._open_wizard()
        wizard.payer_bank_id = False  # The signed XML must still supply the bank.
        wizard.amount = 100.0
        wizard.line_ids[0].allocation_amount = 100.0
        wizard.line_ids[1].allocation_amount = 0.0
        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        self.assertFalse(payment.distributed_payer_bank_id)

        cfdi = etree.Element("Comprobante")
        pagos = etree.SubElement(cfdi, "Pagos")
        etree.SubElement(
            pagos, "Pago", NumOperacion=payment.memo or "TEST-001",
            CtaOrdenante="0164800923", CtaBeneficiario="012580001235126732",
        )
        cfdi_values = payment._get_multiple_invoice_payment_cfdi_bank_values(cfdi)
        values = payment._get_multiple_invoice_payment_bank_report_values(cfdi_values)
        self.assertEqual(values["emitter_account"], "0164800923")
        self.assertEqual(values["receiver_account"], "012580001235126732")

    def test_receipt_matches_each_signed_pago_by_operation_number(self):
        wizard = self._open_wizard()
        wizard.write({
            "amount": 60.0,
            "memo": "TRANSFER-001",
            "use_second_payment": True,
            "second_amount": 40.0,
            "second_journal_id": wizard.journal_id.id,
            "second_payment_method_line_id": wizard.payment_method_line_id.id,
            "second_memo": "TRANSFER-002",
        })
        wizard.action_clear_allocations()
        wizard.line_ids[0].allocation_amount = 60.0
        wizard.line_ids[1].second_allocation_amount = 40.0
        action = wizard.action_create_distributed_payment()
        payments = self.env["account.payment"].browse(action["res_id"]).distributed_group_payment_ids
        self.assertEqual(len(payments), 2)

        cfdi = etree.Element("Comprobante")
        pagos = etree.SubElement(cfdi, "Pagos")
        etree.SubElement(
            pagos, "Pago", NumOperacion="TRANSFER-002", CtaOrdenante="2222222222",
            NomBancoOrdExt="BANCO FICTICIO DOS",
        )
        etree.SubElement(
            pagos, "Pago", NumOperacion="TRANSFER-001", CtaOrdenante="1111111111",
            NomBancoOrdExt="BANCO FICTICIO UNO",
        )
        values = {
            payment.memo: payment._get_multiple_invoice_payment_cfdi_bank_values(cfdi)["payment_info"]
            for payment in payments
        }
        self.assertEqual(values, {
            "TRANSFER-001": {
                "from_account_number": "1111111111",
                "from_account_name": "BANCO FICTICIO UNO",
                "to_account_number": None,
                "to_account_name": None,
            },
            "TRANSFER-002": {
                "from_account_number": "2222222222",
                "from_account_name": "BANCO FICTICIO DOS",
                "to_account_number": None,
                "to_account_name": None,
            },
        })

    def test_bank_report_does_not_guess_emitter_and_keeps_payment_receiver(self):
        self.env["res.partner.bank"].create({
            "acc_number": "012345678901234567",
            "partner_id": self.partner_a.id,
        })
        historical_receiver = self.env["res.partner.bank"].create({
            "acc_number": "111111111111111111",
            "partner_id": self.env.company.partner_id.id,
        })
        current_journal_receiver = self.env["res.partner.bank"].create({
            "acc_number": "222222222222222222",
            "partner_id": self.env.company.partner_id.id,
        })
        wizard = self._open_wizard()
        wizard.payer_bank_id = False  # Do not guess from the contact in this case.
        wizard.amount = 100.0
        wizard.line_ids[0].allocation_amount = 100.0
        wizard.line_ids[1].allocation_amount = 0.0
        wizard.journal_id.bank_account_id = historical_receiver

        action = wizard.action_create_distributed_payment()
        payment = self.env["account.payment"].browse(action["res_id"])
        self.assertEqual(payment.partner_bank_id, historical_receiver)

        wizard.journal_id.bank_account_id = current_journal_receiver
        values = payment._get_multiple_invoice_payment_bank_report_values({})

        self.assertFalse(values["emitter_account"])
        self.assertFalse(values["emitter_bank_name"])
        self.assertEqual(values["receiver_account"], historical_receiver.acc_number)

    def test_optional_studio_remission_switch_and_payment(self):
        wizard = self._open_wizard()

        self.assertEqual(
            wizard._exclude_remission_invoices(self.invoice_1 + self.invoice_2),
            self.invoice_1 + self.invoice_2,
        )
        self.assertEqual(
            self.env["multiple.invoice.payment.wizard"]._fields["memo"].string,
            "Memo",
        )
        wizard_view = self.env.ref(
            "advances_on_multiple_invoices.view_multiple_invoice_payment_wizard_form"
        )
        wizard_arch = etree.fromstring(wizard_view.arch.encode())
        self.assertEqual(
            wizard_arch.xpath("//field[@name='memo']")[0].get("string"),
            "Núm. de operación 1",
        )

        self.invoice_1.ref = "REMISSION"
        self.invoice_2.ref = False
        fake_remission_field = SimpleNamespace(
            name="ref",
            type="boolean",
            store=True,
        )
        with patch.object(
            type(wizard),
            "_get_remission_fields",
            return_value=(fake_remission_field,),
        ):
            self.assertEqual(
                wizard._exclude_remission_invoices(self.invoice_1 + self.invoice_2),
                self.invoice_2,
            )
            self.assertNotIn(self.invoice_1, wizard._find_open_invoices())
            self.assertIn(self.invoice_2, wizard._find_open_invoices())

            wizard.document_kind = "remission"
            self.assertEqual(wizard._find_open_invoices(), self.invoice_1)

            with self.assertRaisesRegex(
                UserError,
                "Invoices and remissions cannot be paid together",
            ):
                self.env["multiple.invoice.payment.wizard"].with_context(
                    active_model="account.move",
                    active_ids=(self.invoice_1 + self.invoice_2).ids,
                ).create({})

            remission_journal = self.env["account.journal"].search([
                ("company_id", "=", self.env.company.id),
                ("type", "in", ("bank", "cash")),
                ("inbound_payment_method_line_ids", "!=", False),
            ], limit=1)
            remission_journal.use_for_remission_payments = True

            remission_wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=self.invoice_1.ids,
            ).create({})
            self.assertEqual(remission_wizard.document_kind, "remission")
            self.assertEqual(remission_wizard.line_ids.invoice_id, self.invoice_1)
            self.assertEqual(remission_wizard.journal_id, remission_journal)

            action = remission_wizard.action_create_distributed_payment()
            payment = self.env["account.payment"].browse(action["res_id"])
            self.assertEqual(payment.distributed_document_kind, "remission")
            self.assertEqual(self.invoice_1.amount_residual, 0.0)
            self.assertTrue(payment.is_reconciled)

    def test_regular_invoices_cannot_use_remission_journal(self):
        wizard = self._open_wizard()
        wizard.journal_id.use_for_remission_payments = True

        with self.assertRaisesRegex(
            ValidationError,
            "does not correspond to the selected document type",
        ):
            wizard._validate_before_payment()


@tagged("post_install", "-at_install")
class TestMultipleInvoicePaymentCfdi(MxEdiCommon):

    def _create_stamped_invoice(self, price_unit, payment_policy):
        invoice = self._create_invoice(
            invoice_line_ids=[Command.create({
                "product_id": self.product.id,
                "price_unit": price_unit,
            })],
        )
        invoice.write({
            "l10n_mx_edi_payment_policy": payment_policy,
            "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
        })
        with self.with_mocked_pac_sign_success():
            invoice._l10n_mx_edi_cfdi_invoice_try_send()
        return invoice

    def test_forgiven_centavo_does_not_inflate_stamped_rep(self):
        with self.mx_external_setup(self.frozen_today):
            invoice = self._create_stamped_invoice(100.0, "PPD")
            balance = invoice.amount_residual
            journal = self.env["account.journal"].search([
                ("type", "=", "general"),
                ("company_id", "=", invoice.company_id.id),
            ], limit=1)
            self.assertTrue(journal)
            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move", active_ids=invoice.ids,
            ).create({
                "amount": balance - 0.01,
                "payment_date": invoice.invoice_date,
                "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
                "shortfall_account_id": self.company_data["default_account_expense"].id,
                "shortfall_journal_id": journal.id,
            })
            wizard.line_ids.allocation_amount = balance - 0.01
            wizard.line_ids.forgive_shortfall = True
            action = wizard.action_create_distributed_payment()
            payment = self.env["account.payment"].browse(action["res_id"])

            self.assertAlmostEqual(invoice.amount_residual, 0.0, places=2)
            self.assertAlmostEqual(payment.amount, balance - 0.01, places=2)
            with self.with_mocked_pac_sign_success():
                invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()
            document = payment.l10n_mx_edi_payment_document_ids.filtered(
                lambda item: item.state == "payment_sent"
            )[:1]
            self.assertTrue(document)
            cfdi_tree = etree.fromstring(document.attachment_id.raw)
            related = cfdi_tree.xpath("//*[local-name()='DoctoRelacionado']")
            self.assertEqual(len(related), 1)
            self.assertAlmostEqual(float(related[0].get("ImpPagado")), balance - 0.01, places=2)
            self.assertAlmostEqual(float(related[0].get("ImpSaldoInsoluto")), 0.01, places=2)

    def test_rp_series_only_counts_signed_ppd_reps_across_journals(self):
        with self.mx_external_setup(self.frozen_today):
            first_journal = self.company_data["default_journal_bank"]
            other_journal = first_journal.copy({
                "name": "Second bank for RP test",
                "code": "RPT",
            })
            other_journal.inbound_payment_method_line_ids[:1].payment_account_id = (
                first_journal.inbound_payment_method_line_ids[:1].payment_account_id
            )

            def pay_invoice(invoice, journal):
                wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                    active_model="account.move",
                    active_ids=invoice.ids,
                ).create({
                    "amount": invoice.amount_residual,
                    "payment_date": invoice.invoice_date,
                    "journal_id": journal.id,
                    "payment_method_line_id": journal.inbound_payment_method_line_ids[:1].id,
                    "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
                })
                wizard.line_ids.allocation_amount = invoice.amount_residual
                action = wizard.action_create_distributed_payment()
                return self.env["account.payment"].browse(action["res_id"])

            first_invoice = self._create_stamped_invoice(100.0, "PPD")
            first_payment = pay_invoice(first_invoice, first_journal)
            self.assertFalse(first_payment.receipt_number)
            with self.assertRaisesRegex(UserError, "timbra el complemento"):
                first_payment._start_receipt_series()
            with self.with_mocked_pac_sign_success():
                first_invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()
            self.assertTrue(first_payment._has_signed_ppd_complement())
            self.assertFalse(first_payment.receipt_number)

            second_invoice = self._create_stamped_invoice(75.0, "PPD")
            second_payment = pay_invoice(second_invoice, other_journal)
            with self.with_mocked_pac_sign_success():
                second_invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()
            self.assertFalse(second_payment.receipt_number)

            first_payment._start_receipt_series()
            self.assertEqual(first_payment.receipt_number, "RP/00001")
            self.assertEqual(second_payment.receipt_number, "RP/00002")

            pue_invoice = self._create_stamped_invoice(50.0, "PUE")
            pue_payment = pay_invoice(pue_invoice, first_journal)
            with self.with_mocked_pac_sign_success():
                pue_invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()
            self.assertFalse(pue_payment._has_signed_ppd_complement())
            self.assertFalse(pue_payment.receipt_number)

            third_invoice = self._create_stamped_invoice(80.0, "PPD")
            third_payment = pay_invoice(third_invoice, first_journal)
            self.assertFalse(third_payment.receipt_number)
            with self.with_mocked_pac_sign_success():
                third_invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()
            self.assertEqual(second_payment.receipt_number, "RP/00002")
            self.assertEqual(third_payment.receipt_number, "RP/00003")
            self.assertFalse(pue_payment.receipt_number)
            self.assertEqual(first_payment.receipt_number, "RP/00001")

    def test_distributed_payment_generates_two_related_ppd_documents(self):
        with self.mx_external_setup(self.frozen_today):
            invoice_1 = self._create_invoice(
                invoice_line_ids=[Command.create({
                    "product_id": self.product.id,
                    "price_unit": 100.0,
                })],
            )
            invoice_2 = self._create_invoice(
                invoice_line_ids=[Command.create({
                    "product_id": self.product.id,
                    "price_unit": 100.0,
                })],
            )
            with self.with_mocked_pac_sign_success():
                invoice_1._l10n_mx_edi_cfdi_invoice_try_send()
                invoice_2._l10n_mx_edi_cfdi_invoice_try_send()

            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=(invoice_1 + invoice_2).ids,
            ).create({
                "amount": 100.0,
                "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
            })
            wizard.line_ids.filtered(
                lambda line: line.invoice_id == invoice_1
            ).allocation_amount = 30.0
            wizard.line_ids.filtered(
                lambda line: line.invoice_id == invoice_2
            ).allocation_amount = 70.0

            action = wizard.action_create_distributed_payment()
            payment = self.env["account.payment"].browse(action["res_id"])

            self.assertTrue(payment.is_reconciled)
            self.assertEqual(
                set(payment.reconciled_invoice_ids.ids),
                set((invoice_1 + invoice_2).ids),
            )
            with self.with_mocked_pac_sign_success():
                invoice_2.l10n_mx_edi_cfdi_invoice_try_update_payments()

            self.assertEqual(payment.l10n_mx_edi_cfdi_state, "sent")
            document = payment.l10n_mx_edi_payment_document_ids.filtered(
                lambda cfdi_document: cfdi_document.state == "payment_sent"
            )[:1]
            self.assertTrue(document)
            cfdi_tree = etree.fromstring(document.attachment_id.raw)
            related_documents = cfdi_tree.xpath(
                "//*[local-name()='DoctoRelacionado']"
            )
            self.assertEqual(len(related_documents), 2)
            self.assertEqual(
                {document_node.get("IdDocumento") for document_node in related_documents},
                set((invoice_1 + invoice_2).mapped("l10n_mx_edi_cfdi_uuid")),
            )
            self.assertEqual(
                {document_node.get("ImpPagado") for document_node in related_documents},
                {"30.00", "70.00"},
            )
            report = self.env.ref("account.action_report_payment_receipt")
            html, _report_type = report._render_qweb_html(
                report.report_name, payment.ids,
            )
            self.assertIn(payment.l10n_mx_edi_cfdi_uuid.encode(), html)
            self.assertIn(b"P - Pago", html)
            receiver_nodes = cfdi_tree.xpath("//*[local-name()='Receptor']")
            self.assertTrue(receiver_nodes)
            self.assertIn(receiver_nodes[0].get("UsoCFDI").encode(), html)
            self.assertIn(b"Fecha de emisi", html)
            self.assertNotIn(b"Serie CFDI:", html)
            self.assertNotIn(b"Folio CFDI:", html)
            concept_table = etree.HTML(html).xpath(
                "//table[@name='multiple_invoice_payment_concepts']"
            )
            self.assertEqual(len(concept_table), 1)
            self.assertIn("font-size: 11px", concept_table[0].get("style", ""))
            self.assertIn("text-align: center", concept_table[0].get("style", ""))
            concept_nodes = cfdi_tree.xpath("//*[local-name()='Concepto']")
            self.assertTrue(concept_nodes)
            concept_text = " ".join(concept_table[0].itertext())
            for field in ("Cantidad", "ClaveProdServ", "ClaveUnidad", "Descripcion", "ValorUnitario", "Importe"):
                self.assertIn(concept_nodes[0].get(field), concept_text)

    def test_two_payment_methods_generate_one_rep_with_two_pago_nodes(self):
        with self.mx_external_setup(self.frozen_today):
            invoice = self._create_stamped_invoice(200.0, "PPD")
            initial_residual = invoice.amount_residual
            transfer = self.env.ref("l10n_mx_edi.payment_method_transferencia")
            compensation = self.env["l10n_mx_edi.payment.method"].search([
                ("code", "=", "17"),
            ], limit=1)
            self.assertTrue(compensation)

            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=invoice.ids,
            ).create({
                "amount": 100.0,
                "memo": "TRANSFER-001",
                "l10n_mx_edi_payment_method_id": transfer.id,
                "use_second_payment": True,
                "second_amount": 50.0,
                "second_memo": "COMP-002",
                "second_journal_id": self.company_data["default_journal_bank"].id,
                "second_payment_method_line_id": self.company_data[
                    "default_journal_bank"
                ].inbound_payment_method_line_ids[:1].id,
                "second_l10n_mx_edi_payment_method_id": compensation.id,
            })
            wizard.line_ids.write({
                "allocation_amount": 100.0,
                "second_allocation_amount": 50.0,
            })

            action = wizard.action_create_distributed_payment()
            primary = self.env["account.payment"].browse(action["res_id"])
            components = primary.distributed_group_payment_ids
            self.assertEqual(len(components), 2)
            self.assertEqual(invoice.amount_residual, invoice.amount_total - 150.0)

            with self.with_mocked_pac_sign_success():
                invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            self.assertEqual(primary.l10n_mx_edi_cfdi_state, "sent")
            self.assertFalse((components - primary).l10n_mx_edi_payment_document_ids)
            document = primary.l10n_mx_edi_payment_document_ids.filtered(
                lambda cfdi_document: cfdi_document.state == "payment_sent"
            )[:1]
            self.assertTrue(document)
            cfdi_tree = etree.fromstring(document.attachment_id.raw)
            payment_nodes = cfdi_tree.xpath("//*[local-name()='Pago']")
            self.assertEqual(len(payment_nodes), 2)
            self.assertEqual(
                [node.get("FormaDePagoP") for node in payment_nodes],
                ["03", "17"],
            )
            self.assertEqual(
                [float(node.get("Monto")) for node in payment_nodes],
                [100.0, 50.0],
            )
            self.assertEqual(
                [node.get("NumOperacion") for node in payment_nodes],
                ["TRANSFER-001", "COMP-002"],
            )
            related_documents = cfdi_tree.xpath(
                "//*[local-name()='DoctoRelacionado']"
            )
            self.assertEqual(len(related_documents), 2)
            self.assertEqual(
                [float(node.get("ImpPagado")) for node in related_documents],
                [100.0, 50.0],
            )
            self.assertEqual(
                [float(node.get("ImpSaldoAnt")) for node in related_documents],
                [initial_residual, initial_residual - 100.0],
            )
            self.assertEqual(
                [float(node.get("ImpSaldoInsoluto")) for node in related_documents],
                [initial_residual - 100.0, initial_residual - 150.0],
            )
            self.assertEqual(
                [node.get("NumParcialidad") for node in related_documents],
                ["1", "2"],
            )
            self.assertEqual(
                [
                    len(node.xpath("./*[local-name()='DoctoRelacionado']"))
                    for node in payment_nodes
                ],
                [1, 1],
            )
            self.assertEqual(
                [
                    float(node.xpath(
                        "./*[local-name()='DoctoRelacionado']"
                    )[0].get("ImpPagado"))
                    for node in payment_nodes
                ],
                [100.0, 50.0],
            )
            totals = cfdi_tree.xpath("//*[local-name()='Totales']")[0]
            self.assertEqual(float(totals.get("MontoTotalPagos")), 150.0)

    def test_third_party_payer_keeps_invoice_customer_as_rep_receiver(self):
        with self.mx_external_setup(self.frozen_today):
            invoice = self._create_stamped_invoice(100.0, "PPD")
            payer, payer_bank = _create_third_party_payer_with_bank(
                self.env,
                suffix="REP",
            )
            transfer_payment_way = self.env.ref(
                "l10n_mx_edi.payment_method_transferencia"
            )
            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=invoice.ids,
            ).create({
                "amount": invoice.amount_residual,
                "payment_date": invoice.invoice_date,
                "payer_id": payer.id,
                "payer_bank_id": payer_bank.id,
                "l10n_mx_edi_payment_method_id": transfer_payment_way.id,
            })

            action = wizard.action_create_distributed_payment()
            payment = self.env["account.payment"].browse(action["res_id"])

            self.assertEqual(payment.partner_id, invoice.commercial_partner_id)
            self.assertEqual(payment.distributed_payer_id, payer.commercial_partner_id)
            self.assertEqual(payment.distributed_payer_bank_id, payer_bank)
            self.assertEqual(invoice.amount_residual, 0.0)

            with self.with_mocked_pac_sign_success():
                invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            self.assertEqual(payment.l10n_mx_edi_cfdi_state, "sent")
            document = payment.l10n_mx_edi_payment_document_ids.filtered(
                lambda cfdi_document: cfdi_document.state == "payment_sent"
            )[:1]
            self.assertTrue(document)
            cfdi_tree = etree.fromstring(document.attachment_id.raw)
            receptor_node = cfdi_tree.xpath("//*[local-name()='Receptor']")[0]
            payment_node = cfdi_tree.xpath("//*[local-name()='Pago']")[0]

            # The REP follows the related invoice. The unrelated payer is
            # represented only by the origin account of the payment.
            self.assertEqual(
                receptor_node.get("Rfc"),
                invoice.l10n_mx_edi_cfdi_customer_rfc,
            )
            self.assertNotEqual(receptor_node.get("Rfc"), payer.vat)
            self.assertEqual(payment_node.get("CtaOrdenante"), payer_bank.acc_number)
            self.assertEqual(
                payment_node.get("RfcEmisorCtaOrd"),
                payer_bank.bank_id.l10n_mx_edi_vat,
            )

    def test_mixed_pue_ppd_payment_only_relates_ppd_invoice(self):
        with self.mx_external_setup(self.frozen_today):
            pue_invoice = self._create_stamped_invoice(50.0, "PUE")
            ppd_invoice = self._create_stamped_invoice(100.0, "PPD")
            pue_amount = pue_invoice.amount_residual
            ppd_amount = 8.0

            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=(pue_invoice + ppd_invoice).ids,
            ).create({
                "amount": pue_amount + ppd_amount,
                "payment_date": pue_invoice.invoice_date,
                "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
            })
            wizard.line_ids.filtered(
                lambda line: line.invoice_id == pue_invoice
            ).allocation_amount = pue_amount
            wizard.line_ids.filtered(
                lambda line: line.invoice_id == ppd_invoice
            ).allocation_amount = ppd_amount

            action = wizard.action_create_distributed_payment()
            payment = self.env["account.payment"].browse(action["res_id"])

            self.assertEqual(payment.amount, pue_amount + ppd_amount)
            self.assertTrue(payment.is_reconciled)
            self.assertRecordValues(pue_invoice + ppd_invoice, [
                {"amount_residual": 0.0},
                {"amount_residual": ppd_invoice.amount_total - ppd_amount},
            ])
            with self.with_mocked_pac_sign_success():
                ppd_invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            self.assertEqual(payment.l10n_mx_edi_cfdi_state, "sent")
            document = payment.l10n_mx_edi_payment_document_ids.filtered(
                lambda cfdi_document: cfdi_document.state == "payment_sent"
            )[:1]
            self.assertTrue(document)
            cfdi_tree = etree.fromstring(document.attachment_id.raw)
            related_documents = cfdi_tree.xpath(
                "//*[local-name()='DoctoRelacionado']"
            )
            self.assertEqual(len(related_documents), 1)
            self.assertEqual(
                related_documents[0].get("IdDocumento"),
                ppd_invoice.l10n_mx_edi_cfdi_uuid,
            )
            self.assertEqual(related_documents[0].get("ImpPagado"), "8.00")
            payment_node = cfdi_tree.xpath("//*[local-name()='Pago']")[0]
            self.assertEqual(float(payment_node.get("Monto")), ppd_amount)

            self.assertIn(document, pue_invoice.l10n_mx_edi_document_ids)
            self.assertNotIn(
                document,
                pue_invoice.multiple_invoice_visible_cfdi_document_ids,
            )
            self.assertIn(
                document,
                ppd_invoice.multiple_invoice_visible_cfdi_document_ids,
            )
            self.assertTrue(
                pue_invoice.multiple_invoice_visible_cfdi_document_ids.filtered(
                    lambda cfdi_document: cfdi_document.state == "invoice_sent"
                )
            )

    def test_ppd_overpayment_posted_to_account_reports_only_applied_amount(self):
        with self.mx_external_setup(self.frozen_today):
            invoice = self._create_stamped_invoice(100.0, "PPD")
            applied_amount = invoice.amount_residual
            excess = 10.0
            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=invoice.ids,
            ).create({
                "amount": applied_amount + excess,
                "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
                "payment_difference_handling": "reconcile",
                "writeoff_account_id": self.company_data["default_account_expense"].id,
                "writeoff_label": "Customer advance",
            })
            wizard.line_ids.allocation_amount = applied_amount

            action = wizard.action_create_distributed_payment()
            payment = self.env["account.payment"].browse(action["res_id"])
            self.assertTrue(payment.is_reconciled)
            self.assertEqual(invoice.amount_residual, 0.0)

            with self.with_mocked_pac_sign_success():
                invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            self.assertEqual(payment.l10n_mx_edi_cfdi_state, "sent")
            document = payment.l10n_mx_edi_payment_document_ids.filtered(
                lambda cfdi_document: cfdi_document.state == "payment_sent"
            )[:1]
            cfdi_tree = etree.fromstring(document.attachment_id.raw)
            related_document = cfdi_tree.xpath(
                "//*[local-name()='DoctoRelacionado']"
            )[0]
            payment_node = cfdi_tree.xpath("//*[local-name()='Pago']")[0]
            self.assertEqual(float(related_document.get("ImpPagado")), applied_amount)
            self.assertEqual(float(payment_node.get("Monto")), applied_amount)

    def test_ppd_unapplied_cents_can_be_posted_with_other_invoices_still_open(self):
        with self.mx_external_setup(self.frozen_today):
            invoice_1 = self._create_stamped_invoice(100.0, "PPD")
            invoice_2 = self._create_stamped_invoice(100.0, "PPD")
            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=(invoice_1 + invoice_2).ids,
            ).create({
                "amount": 100.12,
                "allow_unallocated_amount": True,
                "payment_difference_handling": "reconcile",
                "writeoff_account_id": self.company_data["default_account_expense"].id,
                "writeoff_label": "Diferencia por centavos",
                "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
            })
            wizard.action_clear_allocations()
            wizard.line_ids.filtered(
                lambda line: line.invoice_id == invoice_1
            ).allocation_amount = 60.0
            wizard.line_ids.filtered(
                lambda line: line.invoice_id == invoice_2
            ).allocation_amount = 40.0

            action = wizard.action_create_distributed_payment()
            payment = self.env["account.payment"].browse(action["res_id"])
            self.assertTrue(payment.is_reconciled)
            self.assertEqual(payment.distributed_excess_amount, 0.12)
            self.assertGreater(invoice_1.amount_residual, 0.0)
            self.assertGreater(invoice_2.amount_residual, 0.0)

            with self.with_mocked_pac_sign_success():
                invoice_1.l10n_mx_edi_cfdi_invoice_try_update_payments()

            self.assertEqual(payment.l10n_mx_edi_cfdi_state, "sent")
            document = payment.l10n_mx_edi_payment_document_ids.filtered(
                lambda cfdi_document: cfdi_document.state == "payment_sent"
            )[:1]
            cfdi_tree = etree.fromstring(document.attachment_id.raw)
            related_documents = cfdi_tree.xpath(
                "//*[local-name()='DoctoRelacionado']"
            )
            payment_node = cfdi_tree.xpath("//*[local-name()='Pago']")[0]
            self.assertEqual(
                sorted(float(node.get("ImpPagado")) for node in related_documents),
                [40.0, 60.0],
            )
            self.assertEqual(float(payment_node.get("Monto")), 100.0)

    def test_pue_invoice_must_be_settled_in_full(self):
        with self.mx_external_setup(self.frozen_today):
            pue_invoice = self._create_stamped_invoice(50.0, "PUE")
            partial_amount = pue_invoice.currency_id.round(pue_invoice.amount_residual / 2)
            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=pue_invoice.ids,
            ).create({
                "amount": partial_amount,
                "payment_date": pue_invoice.invoice_date,
                "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
            })
            wizard.line_ids.allocation_amount = partial_amount

            with self.assertRaisesRegex(
                ValidationError,
                "PUE invoices must be settled in full",
            ):
                wizard._validate_before_payment()

    def test_pue_invoice_must_be_paid_within_invoice_month(self):
        with self.mx_external_setup(self.frozen_today):
            pue_invoice = self._create_stamped_invoice(50.0, "PUE")
            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=pue_invoice.ids,
            ).create({
                "amount": pue_invoice.amount_residual,
                "payment_date": pue_invoice.invoice_date + relativedelta(months=1),
                "l10n_mx_edi_payment_method_id": self.payment_method_efectivo.id,
            })

            with self.assertRaisesRegex(
                ValidationError,
                "last day of their invoice month",
            ):
                wizard._validate_before_payment()

    def test_pue_invoice_payment_way_must_match(self):
        with self.mx_external_setup(self.frozen_today):
            pue_invoice = self._create_stamped_invoice(50.0, "PUE")
            transfer_payment_way = self.env.ref("l10n_mx_edi.payment_method_transferencia")
            wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                active_model="account.move",
                active_ids=pue_invoice.ids,
            ).create({
                "amount": pue_invoice.amount_residual,
                "payment_date": pue_invoice.invoice_date,
                "l10n_mx_edi_payment_method_id": transfer_payment_way.id,
            })

            with self.assertRaisesRegex(
                ValidationError,
                "payment way stamped on every PUE invoice",
            ):
                wizard._validate_before_payment()

    def test_remission_payment_is_never_eligible_for_cfdi(self):
        with self.mx_external_setup(self.frozen_today):
            remission = self._create_invoice(
                invoice_line_ids=[Command.create({
                    "product_id": self.product.id,
                    "price_unit": 100.0,
                })],
            )
            remission.ref = "REMISSION"
            remission_journal = self.env["account.journal"].search([
                ("company_id", "=", self.env.company.id),
                ("type", "in", ("bank", "cash")),
                ("inbound_payment_method_line_ids", "!=", False),
            ], limit=1)
            remission_journal.use_for_remission_payments = True
            receiver_bank = self.env["res.partner.bank"].create({
                "acc_number": "012345678901234567",
                "acc_holder_name": "REMISSION COLLECTION ACCOUNT",
                "partner_id": self.env.company.partner_id.id,
                "company_id": self.env.company.id,
            })
            remission_journal.bank_account_id = receiver_bank
            fake_remission_field = SimpleNamespace(
                name="ref",
                type="boolean",
                store=True,
            )

            with (
                patch.object(
                    type(self.env["multiple.invoice.payment.wizard"]),
                    "_get_remission_fields",
                    return_value=(fake_remission_field,),
                ),
                patch.object(
                    type(remission),
                    "_multiple_invoice_is_remission",
                    return_value=True,
                ),
            ):
                self.assertFalse(remission._l10n_mx_edi_is_cfdi_invoice())
                wizard = self.env["multiple.invoice.payment.wizard"].with_context(
                    active_model="account.move",
                    active_ids=remission.ids,
                ).create({})
                action = wizard.action_create_distributed_payment()
                payment = self.env["account.payment"].browse(action["res_id"])

                self.assertEqual(payment.distributed_document_kind, "remission")
                self.assertFalse(payment.move_id._l10n_mx_edi_is_cfdi_payment())
                self.assertFalse(
                    payment.move_id._l10n_mx_edi_cfdi_payment_get_reconciled_invoice_values()
                )
                remission.l10n_mx_edi_cfdi_invoice_try_update_payments()
                self.assertFalse(payment.l10n_mx_edi_payment_document_ids)
                self.assertFalse(payment._has_signed_ppd_complement())
                self.assertFalse(payment.receipt_number)

                report_values = payment._get_multiple_invoice_payment_bank_report_values({})
                self.assertEqual(
                    report_values["receiver_name"],
                    "REMISSION COLLECTION ACCOUNT",
                )
                self.assertEqual(
                    report_values["receiver_company_name"],
                    self.env.company.partner_id.display_name,
                )

                issuer = self.env.ref(
                    "advances_on_multiple_invoices.partner_remission_issuer"
                )
                issuer.write({
                    "name": "EMISOR DE REMISIONES EN RECIBO",
                    "street": "Calle Prueba 123",
                })
                remission.remission_issuer_partner_id = issuer
                report = self.env.ref("account.action_report_payment_receipt")
                html, _report_type = report._render_qweb_html(
                    report.report_name, payment.ids,
                )
                self.assertIn(b"EMISOR DE REMISIONES EN RECIBO", html)
                self.assertIn(b"Documento contable interno", html)
                policy_report = self.env.ref(
                    "advances_on_multiple_invoices.action_report_payment_policy"
                )
                policy_html, _report_type = policy_report._render_qweb_html(
                    policy_report.report_name, payment.ids,
                )
                self.assertIn(b"EMISOR DE REMISIONES EN RECIBO", policy_html)
                self.assertNotIn(b"Calle Prueba 123", policy_html)
                self.assertIn(b"Emisor de remisi", policy_html)
                remission.remission_issuer_partner_id = False
                receipt_without_issuer, _report_type = report._render_qweb_html(
                    report.report_name, payment.ids,
                )
                policy_without_issuer, _report_type = policy_report._render_qweb_html(
                    policy_report.report_name, payment.ids,
                )
                self.assertIn(
                    b"Emisor de remisi\xc3\xb3n no registrado",
                    receipt_without_issuer,
                )
                self.assertIn(b"No registrado en la remisi", policy_without_issuer)


@tagged("post_install", "-at_install")
class TestRemissionSaleAndDeliveryFlow(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.remission_issuer = cls.env.ref(
            "advances_on_multiple_invoices.partner_remission_issuer"
        )
        cls.remission_issuer.write({
            "name": "COMERCIO CENTRALIZADO RS TEST",
            "vat": "XAXX010101000",
            "street": "Calzada del Valle 400 Ext. L19",
            "city": "San Pedro Garza Garcia",
            "zip": "66220",
            "country_id": cls.env.ref("base.mx").id,
        })
        cls.product_remission = cls.env["product.product"].create({
            "name": "Producto para remisión",
            "is_storable": True,
            "list_price": 125.0,
        })
        cls.product_remission.default_code = "REM-TEST"
        sat_product_code = cls.env["product.unspsc.code"].search([
            ("code", "=", "31201610"),
        ], limit=1)
        cls.product_remission.unspsc_code_id = sat_product_code
        cls.remission_journal = cls.env["account.journal"].search([
            ("name", "=", "Remisiones"),
            ("type", "=", "sale"),
            ("company_id", "=", cls.env.company.id),
        ], limit=1) or cls.env["account.journal"].create({
            "name": "Remisiones",
            "type": "sale",
            "code": "RMT99",
            "company_id": cls.env.company.id,
        })

    def _assign_optional_surtido_responsible(self, picking):
        """Meet the picking rule when the optional warehouse module is installed."""
        if "surtido_employee_id" not in picking._fields:
            return
        employee = self.env["hr.employee.public"].sudo().search([
            ("user_id", "=", picking.env.user.id),
            ("company_id", "=", picking.company_id.id),
        ], limit=1)
        if not employee:
            employee = self.env["hr.employee"].sudo().create({
                "name": "Remission test picker",
                "user_id": picking.env.user.id,
                "company_id": picking.company_id.id,
            })
        picking.surtido_employee_id = employee.id

    def test_remission_observations_use_the_order_field_not_terms(self):
        class FakeOrder:
            _fields = {
                "x_studio_observaciones_1": SimpleNamespace(
                    string="Observaciones", type="char",
                ),
                "note": SimpleNamespace(string="Terms and Conditions", type="html"),
            }

            def ensure_one(self):
                return self

            def __getitem__(self, name):
                return {
                    "x_studio_observaciones_1": "SE REEXPIDE TRANSPORTES LOZANO",
                    "note": "NO ES LA OBSERVACIÓN DEL PEDIDO",
                }[name]

        actual = type(self.env["sale.order"])._get_remission_observations_text(
            FakeOrder(),
        )
        self.assertEqual(actual, "SE REEXPIDE TRANSPORTES LOZANO")

    def test_remission_marker_flows_from_sale_to_picking_and_invoice(self):
        sale_view = etree.fromstring(self.env.ref(
            "advances_on_multiple_invoices.sale_order_form_remission"
        ).arch_db.encode())
        print_buttons = sale_view.xpath("//button[@name='action_print_remission']")
        self.assertEqual(len(print_buttons), 1)
        self.assertEqual(print_buttons[0].get("invisible"), "not is_remission")
        self.assertNotIn(
            'name="action_print_remission"',
            self.env.ref(
                "advances_on_multiple_invoices.stock_picking_form_remission"
            ).arch_db,
        )
        order = self.env["sale.order"].sudo().create({
            "partner_id": self.partner_a.id,
            "is_remission": True,
            "order_line": [Command.create({
                "product_id": self.product_remission.id,
                "product_uom_qty": 2.0,
                "price_unit": 125.0,
                "discount": 10.0,
            })],
        })
        self.assertEqual(order.remission_issuer_partner_id, self.remission_issuer)
        self.assertFalse(order.remission_issuer_company_id)

        order.action_confirm()
        picking = order.picking_ids.filtered(
            lambda record: record.picking_type_code == "outgoing"
        )[:1]
        self.assertTrue(picking)
        self.assertTrue(picking.is_remission)
        self.assertEqual(
            picking.remission_issuer_partner_id,
            self.remission_issuer,
        )
        self.assertFalse(order.has_completed_remission_out)
        pending_action = order.action_print_remission()
        self.assertIn(
            pending_action["type"], ("ir.actions.report", "ir.actions.act_window"),
        )
        self.assertFalse(picking._get_remission_report_lines())

        picking.move_ids.quantity = 2.0
        self._assign_optional_surtido_responsible(picking)
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        self.assertTrue(order.has_completed_remission_out)
        report_action = order.action_print_remission()
        self.assertIn(
            report_action["type"], ("ir.actions.report", "ir.actions.act_window"),
        )
        report_lines = picking._get_remission_report_lines()
        self.assertEqual(len(report_lines), 1)
        self.assertEqual(report_lines[0]["quantity"], 2.0)
        self.assertEqual(report_lines[0]["unit_price"], 125.0)
        self.assertEqual(report_lines[0]["discount"], 10.0)
        self.assertEqual(report_lines[0]["subtotal"], 225.0)

        invoice = order._create_invoices()
        self.assertTrue(invoice.is_remission)
        self.assertEqual(invoice.journal_id.name, "Remisiones")
        self.assertEqual(invoice.journal_id.company_id, order.company_id)
        self.assertEqual(
            invoice.remission_issuer_partner_id,
            self.remission_issuer,
        )
        self.assertFalse(invoice._l10n_mx_edi_is_cfdi_invoice())

        report = self.env.ref(
            "advances_on_multiple_invoices.action_report_remission_delivery"
        )
        order.note = "NO USAR TÉRMINOS COMO OBSERVACIONES"
        with patch.object(
            type(order), "_get_remission_transport_text", return_value="NUESTRO CONDUCTO"
        ), patch.object(
            type(order), "_get_remission_observations_text",
            return_value="SE REEXPIDE TRANSPORTES LOZANO",
        ):
            html, _report_type = report._render_qweb_html(
                report.report_name,
                picking.ids,
            )
        self.assertIn(b"REMISI", html)
        self.assertIn(b"COMERCIO CENTRALIZADO RS TEST", html)
        self.assertIn(b"Calzada del Valle 400", html)
        self.assertEqual(html.count(b"Calzada del Valle 400"), 1)
        self.assertNotIn(b"Emisor de la remisi", html)
        parsed_html = etree.HTML(html)
        issuer_headers = parsed_html.xpath("//div[@name='remission_issuer_header']")
        self.assertEqual(len(issuer_headers), 1)
        self.assertIn("font-size: 24px", issuer_headers[0].xpath(".//strong")[0].get("style"))
        title = parsed_html.xpath("//div[@name='remission_title']")
        self.assertEqual(len(title), 1)
        self.assertFalse(title[0].xpath(".//table"))
        self.assertIn("font-size: 14px", title[0].xpath(".//h2")[0].get("style"))
        self.assertEqual(len(parsed_html.xpath("//strong[normalize-space()='Cliente']")), 1)
        closing = parsed_html.xpath("//div[@name='remission_closing']")
        self.assertEqual(len(closing), 1)
        self.assertIn("remission-closing", closing[0].get("class"))
        self.assertNotIn(b"Documento interno no fiscal", html)
        self.assertNotIn(b"Transportista", html)
        self.assertIn(b"Recibi", html)
        self.assertIn(b"Agente:", html)
        self.assertIn(b"Condiciones de pago:", html)
        self.assertIn(b"Transporte:", html)
        self.assertIn(b"NUESTRO CONDUCTO", html)
        self.assertIn(b"SE REEXPIDE TRANSPORTES LOZANO", html)
        self.assertNotIn(b"NO USAR T", html)
        self.assertIn(b"Cantidad", html)
        self.assertIn(b"Clave SAT", html)
        self.assertIn(b"REM-TEST", html)
        if self.product_remission.unspsc_code_id:
            self.assertIn(b"31201610", html)
        self.assertIn(b"P. unitario", html)
        self.assertIn(b"Desc. %", html)
        self.assertIn(b"225.00", html)

        self.remission_issuer.name = "EMISOR ACTUALIZADO DE REMISIONES TEST"
        updated_html, _report_type = report._render_qweb_html(
            report.report_name,
            picking.ids,
        )
        self.assertIn(b"EMISOR ACTUALIZADO DE REMISIONES TEST", updated_html)
        self.assertNotIn(b"COMERCIO CENTRALIZADO RS TEST", updated_html)

    def test_remission_can_print_after_partial_delivery(self):
        order = self.env["sale.order"].sudo().create({
            "partner_id": self.partner_a.id,
            "is_remission": True,
            "order_line": [Command.create({
                "product_id": self.product_remission.id,
                "product_uom_qty": 2.0,
                "price_unit": 125.0,
            })],
        })
        order.action_confirm()
        picking = order.picking_ids.filtered(
            lambda record: record.picking_type_code == "outgoing"
        )[:1]
        picking.move_ids.quantity = 1.0
        self.assertFalse(order.has_completed_remission_out)
        collected_lines = picking._get_remission_report_lines()
        self.assertEqual(len(collected_lines), 1)
        self.assertEqual(collected_lines[0]["quantity"], 1.0)
        self.assertEqual(
            order.with_context(discard_logo_check=True).action_print_remission()["report_name"],
            "advances_on_multiple_invoices.report_remission_delivery",
        )
        self._assign_optional_surtido_responsible(picking)
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, "done")
        self.assertTrue(order.has_completed_remission_out)
        self.assertEqual(order.order_line.qty_delivered, 1.0)
        report_action = order.with_context(discard_logo_check=True).action_print_remission()
        self.assertEqual(
            report_action["report_name"],
            "advances_on_multiple_invoices.report_remission_delivery",
        )
        self.assertEqual(report_action["context"]["active_ids"], picking.ids)

    def test_remission_without_picking_prints_empty_format(self):
        order = self.env["sale.order"].sudo().create({
            "partner_id": self.partner_a.id,
            "is_remission": True,
            "order_line": [Command.create({
                "product_id": self.product_remission.id,
                "product_uom_qty": 2.0,
                "price_unit": 125.0,
            })],
        })
        self.assertFalse(order.picking_ids)
        report_action = order.with_context(discard_logo_check=True).action_print_remission()
        self.assertEqual(
            report_action["report_name"],
            "advances_on_multiple_invoices.report_remission_delivery_empty",
        )
        report = self.env.ref(
            "advances_on_multiple_invoices.action_report_remission_delivery_empty"
        )
        html, _report_type = report._render_qweb_html(report.report_name, order.ids)
        self.assertIn(b"No hay cantidades recogidas", html)
        self.assertIn(b"COMERCIO CENTRALIZADO RS TEST", html)

    def test_remission_requires_completed_issuer_contact(self):
        self.remission_issuer.street = False
        order = self.env["sale.order"].sudo().create({
            "partner_id": self.partner_a.id,
            "is_remission": True,
        })
        with self.assertRaisesRegex(ValidationError, "domicilio"):
            order.action_confirm()

    def test_remission_does_not_use_another_sales_journal(self):
        self.env["account.journal"].search([
            ("name", "=", "Remisiones"),
            ("type", "=", "sale"),
            ("company_id", "=", self.env.company.id),
        ]).write({"name": "Diario temporal sin remisiones"})
        order = self.env["sale.order"].sudo().create({
            "partner_id": self.partner_a.id,
            "is_remission": True,
        })
        with self.assertRaisesRegex(UserError, "Remisiones"):
            order._prepare_invoice()
