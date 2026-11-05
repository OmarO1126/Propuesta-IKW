from markupsafe import Markup

from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import date_utils
from odoo.tools.misc import format_amount


_REMISSION_FIELDS = (
    "is_remission",
    "x_studio_es_una_remision",
    "x_studio_es_una_remision_1",
)

_ORDERING_ACCOUNT_PAYMENT_WAYS = frozenset(("02", "03", "04", "05", "06", "28", "29"))


class MultipleInvoicePaymentWizard(models.TransientModel):
    _name = "multiple.invoice.payment.wizard"
    _description = "Register and distribute a payment over multiple invoices"

    document_kind = fields.Selection(
        selection=[
            ("invoice", "Facturas"),
            ("remission", "Remisiones"),
        ],
        string="Documentos a pagar",
        required=True,
        default="invoice",
    )

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    country_code = fields.Char(related="company_id.account_fiscal_country_id.code")
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Customer",
        required=True,
        domain="[('customer_rank', '>', 0)]",
    )
    payer_id = fields.Many2one(
        comodel_name="res.partner",
        string="Payer",
        required=True,
        help=(
            "Third party that provides the funds. The selected customer "
            "remains the accounting debtor and CFDI receiver."
        ),
    )
    payer_bank_id = fields.Many2one(
        comodel_name="res.partner.bank",
        string="Payer bank account",
        domain="[('id', 'in', available_payer_bank_ids)]",
        check_company=True,
        help=(
            "Optional ordering account to include in the payment receipt "
            "and, when applicable, the REP."
        ),
    )
    available_payer_bank_ids = fields.Many2many(
        comodel_name="res.partner.bank",
        compute="_compute_available_payer_bank_ids",
    )
    payment_date = fields.Date(
        string="Payment date",
        required=True,
        default=fields.Date.context_today,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Payment currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    amount = fields.Monetary(
        string="Payment amount",
        currency_field="currency_id",
        required=True,
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        required=True,
        domain="[('id', 'in', available_journal_ids)]",
    )
    available_journal_ids = fields.Many2many(
        comodel_name="account.journal",
        compute="_compute_available_journal_ids",
    )
    payment_method_line_id = fields.Many2one(
        comodel_name="account.payment.method.line",
        string="Payment method",
        required=True,
        domain="[('id', 'in', available_payment_method_line_ids)]",
    )
    available_payment_method_line_ids = fields.Many2many(
        comodel_name="account.payment.method.line",
        compute="_compute_available_payment_method_line_ids",
    )
    l10n_mx_edi_payment_method_id = fields.Many2one(
        comodel_name="l10n_mx_edi.payment.method",
        string="SAT payment way",
        help="The payment way reported in the CFDI payment complement.",
    )
    l10n_mx_edi_cfdi_origin = fields.Char(
        string="CFDI origin",
        help="Optional replacement relationship, for example 04|UUID.",
    )
    memo = fields.Char(string="Memo")
    policy_observation = fields.Char(
        string="Observación",
        help="Texto sugerido a partir del código del diario y la fecha; puede modificarse antes y después de registrar el cobro.",
    )
    policy_observation_autofill_source = fields.Char(
        string="Sugerencia anterior de observación",
    )
    use_second_payment = fields.Boolean(string="Agregar segunda forma de pago")
    second_amount = fields.Monetary(
        string="Importe 2",
        currency_field="currency_id",
    )
    second_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Diario 2",
        domain="[('id', 'in', available_journal_ids)]",
    )
    second_payment_method_line_id = fields.Many2one(
        comodel_name="account.payment.method.line",
        string="Método de pago 2",
        domain="[('id', 'in', second_available_payment_method_line_ids)]",
    )
    second_available_payment_method_line_ids = fields.Many2many(
        comodel_name="account.payment.method.line",
        compute="_compute_additional_payment_method_line_ids",
    )
    second_l10n_mx_edi_payment_method_id = fields.Many2one(
        comodel_name="l10n_mx_edi.payment.method",
        string="Forma de pago SAT 2",
    )
    second_payer_bank_id = fields.Many2one(
        comodel_name="res.partner.bank",
        string="Cuenta ordenante 2",
        domain="[('id', 'in', available_payer_bank_ids)]",
        check_company=True,
    )
    second_memo = fields.Char(string="Núm. de operación 2")
    use_third_payment = fields.Boolean(string="Agregar tercera forma de pago")
    third_amount = fields.Monetary(
        string="Importe 3",
        currency_field="currency_id",
    )
    third_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Diario 3",
        domain="[('id', 'in', available_journal_ids)]",
    )
    third_payment_method_line_id = fields.Many2one(
        comodel_name="account.payment.method.line",
        string="Método de pago 3",
        domain="[('id', 'in', third_available_payment_method_line_ids)]",
    )
    third_available_payment_method_line_ids = fields.Many2many(
        comodel_name="account.payment.method.line",
        compute="_compute_additional_payment_method_line_ids",
    )
    third_l10n_mx_edi_payment_method_id = fields.Many2one(
        comodel_name="l10n_mx_edi.payment.method",
        string="Forma de pago SAT 3",
    )
    third_payer_bank_id = fields.Many2one(
        comodel_name="res.partner.bank",
        string="Cuenta ordenante 3",
        domain="[('id', 'in', available_payer_bank_ids)]",
        check_company=True,
    )
    third_memo = fields.Char(string="Núm. de operación 3")
    combined_payment_amount = fields.Monetary(
        string="Importe total del pago",
        currency_field="currency_id",
        compute="_compute_combined_payment_amount",
    )
    payment_difference = fields.Monetary(
        string="Payment difference",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    excess_amount = fields.Monetary(
        string="Importe no aplicado",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    has_unallocated_amount = fields.Boolean(compute="_compute_allocation_totals")
    allow_unallocated_amount = fields.Boolean(
        string="Registrar importe no aplicado",
        help=(
            "Permite confirmar el cobro sin aplicar todo su importe a facturas. "
            "La diferencia puede quedar como saldo a favor o registrarse en una cuenta."
        ),
    )
    show_payment_difference = fields.Boolean(compute="_compute_allocation_totals")
    payment_difference_handling = fields.Selection(
        selection=[
            ("open", "Mantener como saldo a favor del cliente"),
            ("reconcile", "Registrar el importe no aplicado en una cuenta"),
        ],
        string="Tratamiento del importe no aplicado",
        required=True,
        default="open",
    )
    writeoff_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Difference account",
        check_company=True,
        copy=False,
    )
    writeoff_label = fields.Char(
        string="Journal item label",
        default=lambda self: _("Diferencia de pago"),
        help="Etiqueta del apunte contable que recibe el importe no aplicado.",
    )
    shortfall_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Cuenta para faltantes perdonados",
        check_company=True,
        default=lambda self: self.env["account.account"].search([
            ("code", "=", "602.84.2"),
            ("company_ids", "in", self.env.company.id),
        ], limit=1),
        help="Cuenta de gasto para centavos que no se cobrarán al cliente.",
    )
    shortfall_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Diario del ajuste por faltante",
        check_company=True,
        default=lambda self: self.env["account.journal"].search([
            ("type", "=", "general"),
            ("company_id", "=", self.env.company.id),
        ], limit=1),
    )
    has_shortfall = fields.Boolean(
        compute="_compute_has_shortfall",
    )
    available_invoice_ids = fields.Many2many(
        comodel_name="account.move",
        compute="_compute_available_invoice_ids",
    )
    invoice_filter_ids = fields.Many2many(
        comodel_name="account.move",
        relation="multiple_invoice_payment_wizard_filter_move_rel",
        column1="wizard_id",
        column2="move_id",
        string="Documentos a mostrar",
        domain="[('id', 'in', available_invoice_ids)]",
        help="Selecciona varias facturas por número o referencia para verlas y distribuirlas juntas sin perder las aplicaciones de las demás.",
    )
    line_ids = fields.One2many(
        comodel_name="multiple.invoice.payment.wizard.line",
        inverse_name="wizard_id",
        string="Invoices",
        copy=False,
    )
    filtered_line_ids = fields.Many2many(
        comodel_name="multiple.invoice.payment.wizard.line",
        relation="multiple_invoice_payment_wizard_filtered_line_rel",
        column1="wizard_id",
        column2="line_id",
        string="Filtered invoices",
        copy=False,
    )
    filtered_second_line_ids = fields.Many2many(
        comodel_name="multiple.invoice.payment.wizard.line",
        relation="multiple_invoice_payment_wizard_filtered_second_rel",
        column1="wizard_id",
        column2="line_id",
        string="Distribución filtrada del método 2",
        copy=False,
    )
    filtered_third_line_ids = fields.Many2many(
        comodel_name="multiple.invoice.payment.wizard.line",
        relation="multiple_invoice_payment_wizard_filtered_third_rel",
        column1="wizard_id",
        column2="line_id",
        string="Distribución filtrada del método 3",
        copy=False,
    )
    invoice_filter_applied = fields.Boolean(copy=False)
    allocated_amount = fields.Monetary(
        string="Allocated",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    first_allocated_amount = fields.Monetary(
        string="Distribuido con método 1",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    second_allocated_amount = fields.Monetary(
        string="Distribuido con método 2",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    third_allocated_amount = fields.Monetary(
        string="Distribuido con método 3",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    first_amount_to_allocate = fields.Monetary(
        string="Pendiente método 1",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    second_amount_to_allocate = fields.Monetary(
        string="Pendiente método 2",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    third_amount_to_allocate = fields.Monetary(
        string="Pendiente método 3",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    amount_to_allocate = fields.Monetary(
        string="Still to allocate",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    allocation_complete = fields.Boolean(compute="_compute_allocation_totals")
    total_outstanding_amount = fields.Monetary(
        string="Total invoice outstanding",
        currency_field="currency_id",
        compute="_compute_allocation_totals",
    )
    amount_exceeds_outstanding = fields.Boolean(compute="_compute_allocation_totals")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        company = self.env["res.company"].browse(values.get("company_id")) or self.env.company
        invoices = self.env["account.move"]

        if self.env.context.get("active_model") == "account.move" and self.env.context.get("active_ids"):
            selected_invoices = self.env["account.move"].browse(
                self.env.context["active_ids"]
            ).exists()
            remission_invoices = self._get_remission_invoices(selected_invoices)
            if remission_invoices and remission_invoices != selected_invoices:
                raise UserError(_(
                    "Invoices and remissions cannot be paid together. Select documents of only one type."
                ))
            document_kind = "remission" if remission_invoices else "invoice"
            invoices = selected_invoices
            self._validate_selected_invoices(invoices, document_kind=document_kind)
            first_invoice = invoices[0]
            company = first_invoice.company_id
            partner = first_invoice.commercial_partner_id
            currency = first_invoice.currency_id
            values.update({
                "company_id": company.id,
                "document_kind": document_kind,
                "partner_id": partner.id,
                "payer_id": values.get("payer_id") or partner.id,
                "currency_id": currency.id,
                "amount": sum(invoices.mapped("amount_residual")),
                "line_ids": [
                    Command.create(self._prepare_wizard_line_values(invoice, invoice.amount_residual, sequence))
                    for sequence, invoice in enumerate(self._sort_invoices(invoices), start=1)
                ],
            })
        else:
            partner = self.env["res.partner"].browse(values.get("partner_id"))
            currency = self.env["res.currency"].browse(values.get("currency_id")) or company.currency_id
            if partner and not values.get("payer_id"):
                values["payer_id"] = partner.commercial_partner_id.id

        document_kind = values.get("document_kind") or "invoice"
        journal = self._get_default_journal(
            company,
            partner,
            currency,
            document_kind=document_kind,
        )
        if journal:
            values["journal_id"] = journal.id
            payment_method_line = journal._get_available_payment_method_lines("inbound")[:1]
            if payment_method_line:
                values["payment_method_line_id"] = payment_method_line.id
            if not values.get("policy_observation"):
                values["policy_observation"] = self._suggest_policy_observation(
                    journal,
                    values.get("payment_date") or fields.Date.context_today(self),
                )
                values["policy_observation_autofill_source"] = values[
                    "policy_observation"
                ]

        sat_payment_way = self._get_default_sat_payment_way(journal, partner)
        if document_kind == "invoice" and sat_payment_way:
            values["l10n_mx_edi_payment_method_id"] = sat_payment_way.id
        if not values.get("payer_bank_id"):
            suggested_bank = self._get_suggested_payer_bank(
                self.env["res.partner"].browse(values.get("payer_id")),
                company, journal, sat_payment_way, document_kind,
            )
            if suggested_bank:
                values["payer_bank_id"] = suggested_bank.id
        return values

    @api.model
    def _get_available_payer_banks(self, payer, company):
        if not payer or not company:
            return self.env["res.partner.bank"]
        return self.env["res.partner.bank"].search([
            ("partner_id", "child_of", payer.commercial_partner_id.id),
            ("company_id", "in", (False, company.id)),
        ])

    @api.model
    def _get_suggested_payer_bank(self, payer, company, journal, sat_method, document_kind):
        """Suggest only one eligible bank; never guess among several accounts."""
        if not journal or journal.type != "bank":
            return self.env["res.partner.bank"]
        if (
            document_kind == "invoice"
            and sat_method
            and sat_method.code not in _ORDERING_ACCOUNT_PAYMENT_WAYS
        ):
            return self.env["res.partner.bank"]
        banks = self._get_available_payer_banks(payer, company)
        return banks if len(banks) == 1 else self.env["res.partner.bank"]

    def _suggest_payer_bank_fields(self):
        self.ensure_one()
        available = self._get_available_payer_banks(self.payer_id, self.company_id)
        for field_name, journal, sat_method, enabled in (
            ("payer_bank_id", self.journal_id, self.l10n_mx_edi_payment_method_id, True),
            ("second_payer_bank_id", self.second_journal_id, self.second_l10n_mx_edi_payment_method_id, self.use_second_payment),
            ("third_payer_bank_id", self.third_journal_id, self.third_l10n_mx_edi_payment_method_id, self.use_third_payment),
        ):
            if self[field_name] and self[field_name] not in available:
                self[field_name] = False
            if enabled and not self[field_name]:
                self[field_name] = self._get_suggested_payer_bank(
                    self.payer_id, self.company_id, journal, sat_method, self.document_kind,
                )

    @api.model
    def _suggest_policy_observation(self, journal, payment_date):
        """The journal code is the variable prefix of Odoo's payment sequence."""
        if not journal or not payment_date:
            return False
        code = (journal.code or "").strip().upper()
        return "%s %s" % (
            code,
            fields.Date.to_date(payment_date).strftime("%d%m%y"),
        ) if code else False

    @api.onchange("journal_id", "payment_date")
    def _onchange_policy_observation(self):
        for wizard in self:
            suggestion = wizard._suggest_policy_observation(
                wizard.journal_id,
                wizard.payment_date,
            )
            if (
                not wizard.policy_observation
                or wizard.policy_observation
                == wizard.policy_observation_autofill_source
            ):
                wizard.policy_observation = suggestion
            wizard.policy_observation_autofill_source = suggestion

    @api.depends("company_id", "document_kind")
    def _compute_available_journal_ids(self):
        for wizard in self:
            journal_types = (
                ("bank", "cash")
                if wizard.document_kind == "remission"
                else ("bank", "cash", "credit")
            )
            wizard.available_journal_ids = self.env["account.journal"].search([
                *self.env["account.journal"]._check_company_domain(wizard.company_id),
                ("type", "in", journal_types),
                (
                    "use_for_remission_payments",
                    "=",
                    wizard.document_kind == "remission",
                ),
                ("inbound_payment_method_line_ids", "!=", False),
            ])

    @api.depends("journal_id")
    def _compute_available_payment_method_line_ids(self):
        for wizard in self:
            wizard.available_payment_method_line_ids = (
                wizard.journal_id._get_available_payment_method_lines("inbound")
                if wizard.journal_id
                else False
            )

    @api.depends("second_journal_id", "third_journal_id")
    def _compute_additional_payment_method_line_ids(self):
        for wizard in self:
            wizard.second_available_payment_method_line_ids = (
                wizard.second_journal_id._get_available_payment_method_lines("inbound")
                if wizard.second_journal_id
                else False
            )
            wizard.third_available_payment_method_line_ids = (
                wizard.third_journal_id._get_available_payment_method_lines("inbound")
                if wizard.third_journal_id
                else False
            )

    @api.depends("payer_id", "company_id")
    def _compute_available_payer_bank_ids(self):
        for wizard in self:
            wizard.available_payer_bank_ids = wizard._get_available_payer_banks(
                wizard.payer_id, wizard.company_id,
            )

    @api.depends("line_ids.invoice_id")
    def _compute_available_invoice_ids(self):
        for wizard in self:
            wizard.available_invoice_ids = wizard.line_ids.invoice_id

    @api.depends(
        "amount",
        "use_second_payment",
        "second_amount",
        "use_third_payment",
        "third_amount",
    )
    def _compute_combined_payment_amount(self):
        for wizard in self:
            wizard.combined_payment_amount = (
                wizard.amount
                + (wizard.second_amount if wizard.use_second_payment else 0.0)
                + (wizard.third_amount if wizard.use_third_payment else 0.0)
            )

    @api.depends(
        "combined_payment_amount",
        "amount",
        "use_second_payment",
        "second_amount",
        "use_third_payment",
        "third_amount",
        "allow_unallocated_amount",
        "line_ids.allocation_amount",
        "line_ids.second_allocation_amount",
        "line_ids.third_allocation_amount",
        "line_ids.current_residual",
        "currency_id",
    )
    def _compute_allocation_totals(self):
        for wizard in self:
            first_allocated = sum(wizard.line_ids.mapped("allocation_amount"))
            second_allocated = sum(wizard.line_ids.mapped("second_allocation_amount"))
            third_allocated = sum(wizard.line_ids.mapped("third_allocation_amount"))
            allocated = first_allocated + second_allocated + third_allocated
            total_outstanding = sum(wizard.line_ids.mapped("current_residual"))
            payment_amount = wizard.combined_payment_amount
            allocation_target = min(max(payment_amount, 0.0), max(total_outstanding, 0.0))
            has_excess = bool(
                wizard.currency_id
                and wizard.currency_id.compare_amounts(payment_amount, total_outstanding) > 0
            )
            has_unallocated = bool(
                wizard.currency_id
                and wizard.currency_id.compare_amounts(payment_amount, allocated) > 0
            )
            unallocated_amount = max(payment_amount - allocated, 0.0)
            wizard.allocated_amount = allocated
            wizard.first_allocated_amount = first_allocated
            wizard.second_allocated_amount = second_allocated
            wizard.third_allocated_amount = third_allocated
            wizard.first_amount_to_allocate = wizard.amount - first_allocated
            wizard.second_amount_to_allocate = (
                wizard.second_amount - second_allocated
                if wizard.use_second_payment
                else 0.0
            )
            wizard.third_amount_to_allocate = (
                wizard.third_amount - third_allocated
                if wizard.use_third_payment
                else 0.0
            )
            wizard.amount_to_allocate = allocation_target - allocated
            wizard.total_outstanding_amount = total_outstanding
            wizard.amount_exceeds_outstanding = has_excess
            wizard.has_unallocated_amount = has_unallocated
            wizard.show_payment_difference = bool(
                has_unallocated
                and (has_excess or wizard.allow_unallocated_amount)
            )
            wizard.payment_difference = (
                -unallocated_amount if wizard.show_payment_difference else 0.0
            )
            wizard.excess_amount = (
                unallocated_amount if wizard.show_payment_difference else 0.0
            )
            wizard.allocation_complete = bool(
                wizard.currency_id
                and wizard.currency_id.compare_amounts(allocated, 0.0) > 0
                and wizard.currency_id.compare_amounts(allocated, payment_amount) <= 0
                and (
                    wizard.currency_id.compare_amounts(payment_amount, allocated) == 0
                    or (
                        has_excess
                        and wizard.currency_id.compare_amounts(
                            allocated,
                            total_outstanding,
                        ) == 0
                    )
                    or wizard.allow_unallocated_amount
                )
            )

    @api.depends("line_ids.forgive_shortfall")
    def _compute_has_shortfall(self):
        for wizard in self:
            wizard.has_shortfall = any(wizard.line_ids.mapped("forgive_shortfall"))

    @api.onchange("company_id")
    def _onchange_company_id(self):
        if not self.company_id:
            return
        if self.currency_id != self.company_id.currency_id:
            self.currency_id = self.company_id.currency_id
        if self.journal_id not in self.available_journal_ids:
            self.journal_id = self._get_default_journal(
                self.company_id,
                self.partner_id,
                self.currency_id,
                document_kind=self.document_kind,
            )
        if self.second_journal_id not in self.available_journal_ids:
            self.second_journal_id = False
        if self.third_journal_id not in self.available_journal_ids:
            self.third_journal_id = False
        if self.shortfall_account_id and self.company_id not in self.shortfall_account_id.company_ids:
            self.shortfall_account_id = False
        if not self.shortfall_account_id:
            self.shortfall_account_id = self.env["account.account"].search([
                ("code", "=", "602.84.2"),
                ("company_ids", "in", self.company_id.id),
            ], limit=1)
        if self.shortfall_journal_id.company_id != self.company_id:
            self.shortfall_journal_id = self.env["account.journal"].search([
                ("type", "=", "general"),
                ("company_id", "=", self.company_id.id),
            ], limit=1)

    @api.onchange("document_kind")
    def _onchange_document_kind(self):
        self._reset_invoice_filter()
        self.line_ids = [Command.clear()]
        self.amount = 0.0
        self.allow_unallocated_amount = False
        self.use_second_payment = False
        self.second_amount = 0.0
        self.second_journal_id = False
        self.second_payment_method_line_id = False
        self.second_l10n_mx_edi_payment_method_id = False
        self.second_payer_bank_id = False
        self.second_memo = False
        self.use_third_payment = False
        self.third_amount = 0.0
        self.third_journal_id = False
        self.third_payment_method_line_id = False
        self.third_l10n_mx_edi_payment_method_id = False
        self.third_payer_bank_id = False
        self.third_memo = False
        if self.document_kind == "remission":
            self.l10n_mx_edi_payment_method_id = False
            self.l10n_mx_edi_cfdi_origin = False
        self.journal_id = self._get_default_journal(
            self.company_id,
            self.partner_id,
            self.currency_id,
            document_kind=self.document_kind,
        )
        self._onchange_journal_id()
        if self.partner_id and self.currency_id and self.company_id:
            invoices = self._find_open_invoices()
            self.line_ids = [Command.clear()] + [
                Command.create(
                    self._prepare_wizard_line_values(invoice, 0.0, sequence)
                )
                for sequence, invoice in enumerate(invoices, start=1)
            ]

    @api.onchange("journal_id")
    def _onchange_journal_id(self):
        if not self.journal_id:
            self.payment_method_line_id = False
            if self.document_kind == "remission":
                self.l10n_mx_edi_payment_method_id = False
                self.l10n_mx_edi_cfdi_origin = False
            return
        available_methods = self.journal_id._get_available_payment_method_lines("inbound")
        if self.payment_method_line_id not in available_methods:
            self.payment_method_line_id = available_methods[:1]
        sat_payment_way = self._get_default_sat_payment_way(self.journal_id, self.partner_id)
        if self.document_kind == "invoice" and sat_payment_way:
            self.l10n_mx_edi_payment_method_id = sat_payment_way
        elif self.document_kind == "remission":
            self.l10n_mx_edi_payment_method_id = False
            self.l10n_mx_edi_cfdi_origin = False
        self._suggest_payer_bank_fields()

    @api.onchange("second_journal_id")
    def _onchange_second_journal_id(self):
        methods = (
            self.second_journal_id._get_available_payment_method_lines("inbound")
            if self.second_journal_id
            else self.env["account.payment.method.line"]
        )
        if self.second_payment_method_line_id not in methods:
            self.second_payment_method_line_id = methods[:1]
        self.second_l10n_mx_edi_payment_method_id = (
            self._get_default_sat_payment_way(self.second_journal_id, self.partner_id)
            if self.document_kind == "invoice" and self.second_journal_id
            else False
        )
        self._suggest_payer_bank_fields()

    @api.onchange("third_journal_id")
    def _onchange_third_journal_id(self):
        methods = (
            self.third_journal_id._get_available_payment_method_lines("inbound")
            if self.third_journal_id
            else self.env["account.payment.method.line"]
        )
        if self.third_payment_method_line_id not in methods:
            self.third_payment_method_line_id = methods[:1]
        self.third_l10n_mx_edi_payment_method_id = (
            self._get_default_sat_payment_way(self.third_journal_id, self.partner_id)
            if self.document_kind == "invoice" and self.third_journal_id
            else False
        )
        self._suggest_payer_bank_fields()

    @api.onchange("payer_id", "company_id")
    def _onchange_payer_id(self):
        self._suggest_payer_bank_fields()

    @api.onchange(
        "l10n_mx_edi_payment_method_id",
        "second_l10n_mx_edi_payment_method_id",
        "third_l10n_mx_edi_payment_method_id",
    )
    def _onchange_sat_payment_method(self):
        self._suggest_payer_bank_fields()

    @api.onchange("partner_id", "currency_id")
    def _onchange_partner_or_currency(self):
        if self.partner_id and not self.payer_id:
            self.payer_id = self.partner_id.commercial_partner_id
        self._suggest_payer_bank_fields()
        self._reset_invoice_filter()
        if not self.partner_id or not self.currency_id or not self.company_id:
            self.line_ids = [Command.clear()]
            return
        active_invoices = self.env["account.move"]
        if (
            self.env.context.get("active_model") == "account.move"
            and self.env.context.get("active_ids")
        ):
            active_invoices = self.env["account.move"].browse(
                self.env.context["active_ids"]
            ).exists()
            active_invoices = self._filter_documents_by_kind(active_invoices)
        # Preserve the exact allocations prepared by default_get when the form
        # opens from a selection of invoices. The web client triggers this
        # onchange once while initializing the dialog.
        if (
            active_invoices
            and set(self.line_ids.invoice_id.ids) == set(active_invoices.ids)
            and active_invoices.company_id == self.company_id
            and active_invoices.commercial_partner_id == self.partner_id.commercial_partner_id
            and active_invoices.currency_id == self.currency_id
        ):
            return
        invoices = self._find_open_invoices()
        self.line_ids = [Command.clear()] + [
            Command.create(self._prepare_wizard_line_values(invoice, 0.0, sequence))
            for sequence, invoice in enumerate(invoices, start=1)
        ]
        self.amount = 0.0
        self.allow_unallocated_amount = False
        self.use_second_payment = False
        self.use_third_payment = False
        self.second_amount = 0.0
        self.third_amount = 0.0

    def action_add_second_payment(self):
        self.ensure_one()
        self.use_second_payment = True
        if not self.second_journal_id:
            self.second_journal_id = self._get_default_journal(
                self.company_id,
                self.partner_id,
                self.currency_id,
                document_kind=self.document_kind,
            )
            self._onchange_second_journal_id()
        return self._reopen()

    def action_remove_second_payment(self):
        self.ensure_one()
        self.line_ids.write({
            "second_allocation_amount": 0.0,
            "third_allocation_amount": 0.0,
        })
        self.use_second_payment = False
        self.use_third_payment = False
        self.second_amount = 0.0
        self.third_amount = 0.0
        self.second_journal_id = False
        self.second_payment_method_line_id = False
        self.second_l10n_mx_edi_payment_method_id = False
        self.second_payer_bank_id = False
        self.second_memo = False
        self.third_journal_id = False
        self.third_payment_method_line_id = False
        self.third_l10n_mx_edi_payment_method_id = False
        self.third_payer_bank_id = False
        self.third_memo = False
        return self._reopen()

    def action_add_third_payment(self):
        self.ensure_one()
        self.use_second_payment = True
        self.use_third_payment = True
        if not self.third_journal_id:
            self.third_journal_id = self._get_default_journal(
                self.company_id,
                self.partner_id,
                self.currency_id,
                document_kind=self.document_kind,
            )
            self._onchange_third_journal_id()
        return self._reopen()

    def action_remove_third_payment(self):
        self.ensure_one()
        self.line_ids.third_allocation_amount = 0.0
        self.use_third_payment = False
        self.third_amount = 0.0
        return self._reopen()

    @api.model
    def _get_default_journal(
        self,
        company,
        partner,
        currency,
        document_kind="invoice",
    ):
        if not company:
            return self.env["account.journal"]
        if partner and document_kind == "invoice":
            preferred_method = partner.with_company(company).property_inbound_payment_method_line_id
            if (
                preferred_method
                and preferred_method.journal_id.company_id == company
                and not preferred_method.journal_id.use_for_remission_payments
            ):
                return preferred_method.journal_id
        journal_types = (
            ("bank", "cash")
            if document_kind == "remission"
            else ("bank", "cash", "credit")
        )
        journals = self.env["account.journal"].search([
            *self.env["account.journal"]._check_company_domain(company),
            ("type", "in", journal_types),
            (
                "use_for_remission_payments",
                "=",
                document_kind == "remission",
            ),
            ("inbound_payment_method_line_ids", "!=", False),
        ])
        if currency:
            matching_currency = journals.filtered(lambda journal: journal.currency_id == currency)
            if matching_currency:
                return matching_currency[:1]
            no_currency = journals.filtered(lambda journal: not journal.currency_id)
            if no_currency:
                return no_currency[:1]
        return journals[:1]

    @api.model
    def _get_default_sat_payment_way(self, journal, partner):
        candidates = (
            journal.l10n_mx_edi_payment_method_id
            | partner.l10n_mx_edi_payment_method_id
            if journal and partner
            else journal.l10n_mx_edi_payment_method_id
            if journal
            else partner.l10n_mx_edi_payment_method_id
            if partner
            else self.env["l10n_mx_edi.payment.method"]
        )
        return candidates.filtered(lambda method: method.active and method.code != "99")[:1]

    @api.model
    def _sort_invoices(self, invoices, reverse=False):
        return invoices.sorted(
            key=lambda invoice: (
                invoice.invoice_date_due or invoice.invoice_date or invoice.date,
                invoice.invoice_date or invoice.date,
                invoice.name or "",
                invoice.id,
            ),
            reverse=reverse,
        )

    @api.model
    def _get_open_receivable_lines(self, invoice):
        return invoice.line_ids.filtered(
            lambda line: (
                line.account_type == "asset_receivable"
                and line.display_type == "payment_term"
                and not line.reconciled
            )
        )

    @api.model
    def _prepare_wizard_line_values(self, invoice, allocation_amount, sequence):
        receivable_lines = self._get_open_receivable_lines(invoice)
        return {
            "sequence": sequence,
            "invoice_id": invoice.id,
            "account_id": receivable_lines.account_id[:1].id,
            "initial_residual": invoice.amount_residual,
            "allocation_amount": allocation_amount,
        }

    @api.model
    def _get_remission_field(self):
        """Return the native marker, or an available legacy Studio field."""
        return next(iter(self._get_remission_fields()), None)

    @api.model
    def _get_remission_fields(self):
        """Return native and available legacy remission markers."""
        move_fields = self.env["account.move"]._fields
        return tuple(
            move_fields[field_name]
            for field_name in _REMISSION_FIELDS
            if field_name in move_fields and move_fields[field_name].type == "boolean"
        )

    @api.model
    def _get_remission_invoices(self, invoices):
        fields_available = self._get_remission_fields()
        if not fields_available:
            return self.env["account.move"]
        remission_ids = set()
        for field in fields_available:
            # Keep the user's record rules on the candidate set. Elevate only
            # the Boolean read in case Studio restricted it to some groups.
            remission_ids.update(
                invoices.sudo().filtered(lambda invoice: invoice[field.name]).ids
            )
        return invoices.filtered(lambda invoice: invoice.id in remission_ids)

    @api.model
    def _exclude_remission_invoices(self, invoices):
        return invoices - self._get_remission_invoices(invoices)

    def _filter_documents_by_kind(self, invoices, document_kind=None):
        document_kind = document_kind or self.document_kind or "invoice"
        if document_kind == "remission":
            return self._get_remission_invoices(invoices)
        return self._exclude_remission_invoices(invoices)

    def _find_open_invoices(self):
        self.ensure_one()
        invoices = self.env["account.move"].search([
            ("company_id", "=", self.company_id.id),
            ("commercial_partner_id", "=", self.partner_id.commercial_partner_id.id),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("currency_id", "=", self.currency_id.id),
            ("amount_residual", ">", 0.0),
        ])
        return self._sort_invoices(self._filter_documents_by_kind(invoices))

    @api.model
    def _validate_selected_invoices(self, invoices, document_kind=None):
        if not invoices:
            raise UserError(_("Select at least one customer invoice."))
        document_kind = document_kind or self.document_kind or "invoice"
        remission_invoices = self._get_remission_invoices(invoices)
        if remission_invoices and remission_invoices != invoices:
            raise UserError(_(
                "Invoices and remissions cannot be paid together. Select documents of only one type."
            ))
        if document_kind == "invoice" and remission_invoices:
            raise UserError(_("The selected documents are remissions, not invoices."))
        if document_kind == "remission" and not remission_invoices:
            raise UserError(_("The selected documents are invoices, not remissions."))
        invalid = invoices.filtered(
            lambda invoice: (
                invoice.move_type != "out_invoice"
                or invoice.state != "posted"
                or invoice.currency_id.is_zero(invoice.amount_residual)
            )
        )
        if invalid:
            raise UserError(_(
                "Only posted customer invoices with an outstanding balance can be distributed. Invalid documents: %(documents)s",
                documents=", ".join(invalid.mapped("display_name")),
            ))
        if len(invoices.company_id) != 1:
            raise UserError(_("All invoices must belong to the same company."))
        if len(invoices.commercial_partner_id) != 1:
            raise UserError(_("All invoices must belong to the same commercial customer."))
        if len(invoices.currency_id) != 1:
            raise UserError(_("All invoices must use the same currency."))
        accounts = invoices.line_ids.filtered(
            lambda line: line.account_type == "asset_receivable" and not line.reconciled
        ).account_id
        if len(accounts) != 1:
            raise UserError(_("All invoices must use the same receivable account."))

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Register and distribute payment"),
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def action_allocate_oldest(self):
        self.ensure_one()
        self._allocate_component_sequentially(
            1,
            self._sort_invoices(self._allocation_scope_lines().invoice_id),
        )
        return self._reopen()

    def action_allocate_newest(self):
        self.ensure_one()
        self._allocate_component_sequentially(
            1,
            self._sort_invoices(self._allocation_scope_lines().invoice_id, reverse=True),
        )
        return self._reopen()

    def _allocation_scope_lines(self):
        self.ensure_one()
        return self.filtered_line_ids if self.invoice_filter_applied else self.line_ids

    @api.model
    def _get_component_allocation_field(self, sequence):
        return {
            1: "allocation_amount",
            2: "second_allocation_amount",
            3: "third_allocation_amount",
        }[sequence]

    def _get_component_amount(self, sequence):
        self.ensure_one()
        return {
            1: self.amount,
            2: self.second_amount if self.use_second_payment else 0.0,
            3: self.third_amount if self.use_third_payment else 0.0,
        }[sequence]

    @api.model
    def _get_line_other_component_allocation(self, line, sequence):
        fields_by_sequence = {
            1: "allocation_amount",
            2: "second_allocation_amount",
            3: "third_allocation_amount",
        }
        return sum(
            line[field_name]
            for component_sequence, field_name in fields_by_sequence.items()
            if component_sequence != sequence
        )

    def _allocate_component_sequentially(self, sequence, ordered_invoices):
        line_by_invoice = {line.invoice_id: line for line in self.line_ids}
        field_name = self._get_component_allocation_field(sequence)
        scope_lines = self._allocation_scope_lines()
        outside_allocated = sum((self.line_ids - scope_lines).mapped(field_name))
        remaining = max(self._get_component_amount(sequence) - outside_allocated, 0.0)
        for line in scope_lines:
            line[field_name] = 0.0
        for invoice in ordered_invoices:
            if self.currency_id.is_zero(remaining):
                break
            line = line_by_invoice[invoice]
            available_residual = max(
                invoice.amount_residual
                - self._get_line_other_component_allocation(line, sequence),
                0.0,
            )
            allocation = min(available_residual, remaining)
            line[field_name] = allocation
            remaining -= allocation

    def action_allocate_second_oldest(self):
        self.ensure_one()
        self._allocate_component_sequentially(
            2,
            self._sort_invoices(self._allocation_scope_lines().invoice_id),
        )
        return self._reopen()

    def action_allocate_second_newest(self):
        self.ensure_one()
        self._allocate_component_sequentially(
            2,
            self._sort_invoices(self._allocation_scope_lines().invoice_id, reverse=True),
        )
        return self._reopen()

    def action_allocate_third_oldest(self):
        self.ensure_one()
        self._allocate_component_sequentially(
            3,
            self._sort_invoices(self._allocation_scope_lines().invoice_id),
        )
        return self._reopen()

    def action_allocate_third_newest(self):
        self.ensure_one()
        self._allocate_component_sequentially(
            3,
            self._sort_invoices(self._allocation_scope_lines().invoice_id, reverse=True),
        )
        return self._reopen()

    def action_allocate_proportionally(self):
        self.ensure_one()
        self._allocate_component_proportionally(1)
        return self._reopen()

    def _allocate_component_proportionally(self, sequence):
        self.ensure_one()
        field_name = self._get_component_allocation_field(sequence)
        scope_lines = self._allocation_scope_lines()
        available_by_line = {
            line: max(
                line.current_residual
                - self._get_line_other_component_allocation(line, sequence),
                0.0,
            )
            for line in scope_lines
        }
        eligible_lines = scope_lines.filtered(
            lambda line: available_by_line[line] > 0.0
        )
        for line in scope_lines:
            line[field_name] = 0.0
        total_residual = sum(available_by_line[line] for line in eligible_lines)
        outside_allocated = sum((self.line_ids - scope_lines).mapped(field_name))
        target = min(
            max(self._get_component_amount(sequence) - outside_allocated, 0.0),
            total_residual,
        )
        if self.currency_id.is_zero(target) or self.currency_id.is_zero(total_residual):
            return

        remaining = target
        for line in eligible_lines[:-1]:
            proportional = self.currency_id.round(
                target * available_by_line[line] / total_residual
            )
            allocation = min(proportional, available_by_line[line], remaining)
            line[field_name] = allocation
            remaining -= allocation
        if eligible_lines:
            last_line = eligible_lines[-1]
            last_line[field_name] = min(available_by_line[last_line], remaining)

    def action_allocate_second_proportionally(self):
        self.ensure_one()
        self._allocate_component_proportionally(2)
        return self._reopen()

    def action_allocate_third_proportionally(self):
        self.ensure_one()
        self._allocate_component_proportionally(3)
        return self._reopen()

    def action_clear_allocations(self):
        self.ensure_one()
        self.line_ids.write({
            "allocation_amount": 0.0,
            "second_allocation_amount": 0.0,
            "third_allocation_amount": 0.0,
        })
        return self._reopen()

    def action_clear_first_allocations(self):
        self.ensure_one()
        self._allocation_scope_lines().allocation_amount = 0.0
        return self._reopen()

    def action_clear_second_allocations(self):
        self.ensure_one()
        self._allocation_scope_lines().second_allocation_amount = 0.0
        return self._reopen()

    def action_clear_third_allocations(self):
        self.ensure_one()
        self._allocation_scope_lines().third_allocation_amount = 0.0
        return self._reopen()

    def _reset_invoice_filter(self):
        self.ensure_one()
        self.invoice_filter_ids = [Command.clear()]
        self.invoice_filter_applied = False
        self.filtered_line_ids = [Command.clear()]
        self.filtered_second_line_ids = [Command.clear()]
        self.filtered_third_line_ids = [Command.clear()]

    def action_apply_invoice_filter(self):
        self.ensure_one()
        if not self.invoice_filter_ids:
            raise ValidationError(_("Select at least one invoice to filter."))
        selected_lines = self.line_ids.filtered(
            lambda line: line.invoice_id in self.invoice_filter_ids
        )
        if len(selected_lines) != len(self.invoice_filter_ids):
            raise ValidationError(_("Some selected invoices are no longer available in this wizard."))
        self.filtered_line_ids = [Command.set(selected_lines.ids)]
        self.filtered_second_line_ids = [Command.set(selected_lines.ids)]
        self.filtered_third_line_ids = [Command.set(selected_lines.ids)]
        self.invoice_filter_applied = True
        return self._reopen()

    def action_clear_invoice_filter(self):
        self.ensure_one()
        self._reset_invoice_filter()
        return self._reopen()

    def _get_payment_components(self):
        """Return the enabled payment methods in their fiscal/accounting order."""
        self.ensure_one()
        components = [{
            "sequence": 1,
            "amount": self.amount,
            "journal": self.journal_id,
            "payment_method_line": self.payment_method_line_id,
            "sat_payment_method": self.l10n_mx_edi_payment_method_id,
            "payer_bank": self.payer_bank_id,
            "memo": self.memo,
        }]
        if self.use_second_payment:
            components.append({
                "sequence": 2,
                "amount": self.second_amount,
                "journal": self.second_journal_id,
                "payment_method_line": self.second_payment_method_line_id,
                "sat_payment_method": self.second_l10n_mx_edi_payment_method_id,
                "payer_bank": self.second_payer_bank_id,
                "memo": self.second_memo,
            })
        if self.use_third_payment:
            components.append({
                "sequence": 3,
                "amount": self.third_amount,
                "journal": self.third_journal_id,
                "payment_method_line": self.third_payment_method_line_id,
                "sat_payment_method": self.third_l10n_mx_edi_payment_method_id,
                "payer_bank": self.third_payer_bank_id,
                "memo": self.third_memo,
            })
        return components

    def _validate_payment_components(self, is_remission):
        self.ensure_one()
        if self.use_third_payment and not self.use_second_payment:
            raise ValidationError(_(
                "Enable the second payment method before adding a third one."
            ))
        payer = self.payer_id.commercial_partner_id
        components = self._get_payment_components()
        for component in components:
            number = component["sequence"]
            amount = component["amount"]
            journal = component["journal"]
            method_line = component["payment_method_line"]
            payer_bank = component["payer_bank"]
            sat_method = component["sat_payment_method"]

            if self.currency_id.compare_amounts(amount, 0.0) <= 0:
                raise ValidationError(_(
                    "Payment method %(number)s must have an amount greater than zero.",
                    number=number,
                ))
            if not journal or journal.company_id != self.company_id:
                raise ValidationError(_(
                    "Select a journal from the payment company for payment method %(number)s.",
                    number=number,
                ))
            if bool(journal.use_for_remission_payments) != is_remission:
                raise ValidationError(_(
                    "Payment method %(number)s uses a journal that does not correspond to the selected document type.",
                    number=number,
                ))
            if method_line not in journal._get_available_payment_method_lines("inbound"):
                raise ValidationError(_(
                    "Select an inbound payment method for payment method %(number)s.",
                    number=number,
                ))
            if not method_line.payment_account_id:
                raise ValidationError(_(
                    "The payment method must have an outstanding receipts account. Configure it on journal %(journal)s before using this wizard.",
                    journal=journal.display_name,
                ))
            if payer_bank:
                if payer_bank.partner_id.commercial_partner_id != payer:
                    raise ValidationError(_(
                        "The ordering bank account for payment method %(number)s must belong to the selected payer.",
                        number=number,
                    ))
                if payer_bank.company_id and payer_bank.company_id != self.company_id:
                    raise ValidationError(_(
                        "The ordering bank account for payment method %(number)s is not available to the selected company.",
                        number=number,
                    ))
            if (
                self.company_id.account_fiscal_country_id.code == "MX"
                and not is_remission
                and (
                    not sat_method
                    or not sat_method.active
                    or sat_method.code == "99"
                )
            ):
                raise ValidationError(_(
                    "Select a valid SAT payment way for payment method %(number)s.",
                    number=number,
                ))
        return components

    def _validate_before_payment(self):
        self.ensure_one()
        is_remission = self.document_kind == "remission"
        if is_remission and not self._get_remission_fields():
            raise ValidationError(_(
                "The remission marker is not available on customer invoices."
            ))
        if not self.payer_id:
            raise ValidationError(_("Select the person or company that provided the funds."))
        if not self.line_ids:
            raise ValidationError(_("There are no invoices available to distribute the payment."))
        components = self._validate_payment_components(is_remission)
        payment_amount = self.combined_payment_amount

        loaded_invoices = self.line_ids.invoice_id
        self.company_id._with_locked_records(loaded_invoices)
        fields_to_invalidate = ["amount_residual", "payment_state"]
        fields_to_invalidate += [
            field.name
            for field in self._get_remission_fields()
            if field.store
        ]
        loaded_invoices.invalidate_recordset(fields_to_invalidate)

        if (
            not self.use_second_payment
            and any(self.line_ids.mapped("second_allocation_amount"))
        ):
            raise ValidationError(_(
                "Clear the allocations of payment method 2 before removing it."
            ))
        if (
            not self.use_third_payment
            and any(self.line_ids.mapped("third_allocation_amount"))
        ):
            raise ValidationError(_(
                "Clear the allocations of payment method 3 before removing it."
            ))
        negative_lines = self.line_ids.filtered(lambda line: (
            line.allocation_amount < 0.0
            or line.second_allocation_amount < 0.0
            or line.third_allocation_amount < 0.0
        ))
        if negative_lines:
            raise ValidationError(_("Applied amounts cannot be negative."))
        distribution_lines = self.line_ids.filtered(
            lambda line: self.currency_id.compare_amounts(
                line.total_allocation_amount,
                0.0,
            ) > 0
        )
        if not distribution_lines:
            raise ValidationError(_("Apply the payment to at least one invoice."))
        allocated = sum(distribution_lines.mapped("total_allocation_amount"))
        current_total_outstanding = sum(self.line_ids.mapped("current_residual"))
        has_excess = self.currency_id.compare_amounts(
            payment_amount,
            current_total_outstanding,
        ) > 0
        if self.currency_id.compare_amounts(allocated, payment_amount) > 0:
            raise ValidationError(_(
                "The allocated amount cannot exceed the payment. Payment: %(payment)s; allocated: %(allocated)s.",
                payment=payment_amount,
                allocated=allocated,
            ))
        unallocated_amount = max(payment_amount - allocated, 0.0)
        has_unallocated = self.currency_id.compare_amounts(
            unallocated_amount,
            0.0,
        ) > 0
        if (
            has_excess
            and self.currency_id.compare_amounts(
                allocated,
                current_total_outstanding,
            ) != 0
        ):
            raise ValidationError(_(
                "All loaded invoice balances must be allocated before an excess can be posted. "
                "Invoice balance: %(balance)s; allocated: %(allocated)s.",
                balance=current_total_outstanding,
                allocated=allocated,
            ))
        if has_unallocated and not has_excess and not self.allow_unallocated_amount:
            raise ValidationError(_(
                "The complete payment must be allocated, or you must enable the option to register the unapplied amount. Payment: %(payment)s; allocated: %(allocated)s.",
                payment=payment_amount,
                allocated=allocated,
            ))

        for component in components:
            allocation_field = self._get_component_allocation_field(
                component["sequence"]
            )
            component_allocated = sum(self.line_ids.mapped(allocation_field))
            if self.currency_id.compare_amounts(
                component_allocated,
                component["amount"],
            ) > 0:
                raise ValidationError(_(
                    "Payment method %(number)s has %(allocated)s allocated, but its amount is only %(amount)s.",
                    number=component["sequence"],
                    allocated=component_allocated,
                    amount=component["amount"],
                ))

        if has_unallocated and self.payment_difference_handling == "reconcile":
            if not self.writeoff_account_id:
                raise ValidationError(_("Select the account that will receive the unapplied amount."))
            if self.company_id not in self.writeoff_account_id.company_ids:
                raise ValidationError(_("The difference account must belong to the selected company."))
            if self.writeoff_account_id.account_type in (
                "asset_receivable",
                "liability_payable",
                "asset_cash",
                "off_balance",
            ):
                raise ValidationError(_(
                    "Use a regular balance-sheet or profit-and-loss account for the unapplied amount."
                ))
            if not (self.writeoff_label or "").strip():
                raise ValidationError(_("Enter a label for the unapplied amount journal item."))

        invoices = distribution_lines.invoice_id
        self._validate_selected_invoices(invoices)
        if self.partner_id.commercial_partner_id != invoices.commercial_partner_id:
            raise ValidationError(_("The selected invoices no longer belong to the selected customer."))
        if self.currency_id != invoices.currency_id:
            raise ValidationError(_("The payment and every selected invoice must use the same currency."))

        forgiven_lines = self.line_ids.filtered("forgive_shortfall")
        if forgiven_lines:
            if is_remission:
                raise ValidationError(_("A remission cannot use the invoice shortfall adjustment."))
            if self.currency_id != self.company_id.currency_id:
                raise ValidationError(_("Small shortfall adjustments require the company currency."))
            if has_unallocated:
                raise ValidationError(_("Allocate the entire actual payment before forgiving an invoice shortfall."))
            if not self.shortfall_account_id or self.shortfall_account_id.account_type != "expense":
                raise ValidationError(_("Select an expense account for the forgiven shortfall."))
            if self.company_id not in self.shortfall_account_id.company_ids:
                raise ValidationError(_("The shortfall expense account must belong to the selected company."))
            if not self.shortfall_journal_id or self.shortfall_journal_id.type != "general":
                raise ValidationError(_("Select a miscellaneous journal for the shortfall adjustment."))
            if self.shortfall_journal_id.company_id != self.company_id:
                raise ValidationError(_("The shortfall journal must belong to the selected company."))
            for line in forgiven_lines:
                shortfall = line.current_residual - line.total_allocation_amount
                if line not in distribution_lines or self.currency_id.compare_amounts(shortfall, 0.0) <= 0:
                    raise ValidationError(_(
                        "Apply a real payment smaller than the balance before forgiving the shortfall on %(invoice)s.",
                        invoice=line.invoice_id.display_name,
                    ))
                if self.currency_id.compare_amounts(shortfall, 1.0) >= 0:
                    raise ValidationError(_(
                        "Only centavo differences below 1.00 may be forgiven on %(invoice)s.",
                        invoice=line.invoice_id.display_name,
                    ))
                if self.company_id.account_fiscal_country_id.code == "MX" and line.invoice_id.l10n_mx_edi_payment_policy != "PPD":
                    raise ValidationError(_("Only PPD invoices may use this shortfall adjustment."))

        for line in distribution_lines:
            invoice = line.invoice_id
            open_receivable_lines = self._get_open_receivable_lines(invoice)
            if len(open_receivable_lines.account_id) != 1:
                raise ValidationError(_(
                    "Invoice %(invoice)s no longer has one open receivable account. Reload the wizard.",
                    invoice=invoice.display_name,
                ))
            line.account_id = open_receivable_lines.account_id
            if self.currency_id.compare_amounts(
                line.total_allocation_amount,
                invoice.amount_residual,
            ) > 0:
                raise ValidationError(_(
                    "Invoice %(invoice)s now has only %(residual)s outstanding. Reload the wizard and redistribute the payment.",
                    invoice=invoice.display_name,
                    residual=invoice.amount_residual,
                ))

        if len(distribution_lines.account_id) != 1:
            raise ValidationError(_(
                "All selected invoices must use the same receivable account."
            ))

        if self.company_id.account_fiscal_country_id.code == "MX" and not is_remission:
            unsigned_invoices = invoices.filtered(
                lambda invoice: (
                    invoice.l10n_mx_edi_cfdi_state not in ("sent", "global_sent")
                    or not invoice.l10n_mx_edi_cfdi_uuid
                )
            )
            if unsigned_invoices:
                raise ValidationError(_(
                    "Every Mexican invoice must have a valid stamped CFDI before receiving this payment. Pending documents: %(documents)s",
                    documents=", ".join(unsigned_invoices.mapped("display_name")),
                ))
            unsupported_policy_invoices = invoices.filtered(
                lambda invoice: invoice.l10n_mx_edi_payment_policy not in ("PUE", "PPD")
            )
            if unsupported_policy_invoices:
                raise ValidationError(_(
                    "Every Mexican invoice must use payment policy PUE or PPD. "
                    "Invalid documents: %(documents)s",
                    documents=", ".join(unsupported_policy_invoices.mapped("display_name")),
                ))

            pue_lines = distribution_lines.filtered(
                lambda line: line.invoice_id.l10n_mx_edi_payment_policy == "PUE"
            )
            partially_paid_pue_lines = pue_lines.filtered(
                lambda line: self.currency_id.compare_amounts(
                    line.total_allocation_amount,
                    line.invoice_id.amount_residual,
                ) != 0
            )
            if partially_paid_pue_lines:
                raise ValidationError(_(
                    "PUE invoices must be settled in full by this payment. "
                    "Documents with a remaining balance: %(documents)s",
                    documents=", ".join(partially_paid_pue_lines.invoice_id.mapped("display_name")),
                ))

            late_pue_invoices = pue_lines.invoice_id.filtered(
                lambda invoice: (
                    not invoice.invoice_date
                    or self.payment_date > date_utils.end_of(invoice.invoice_date, "month")
                )
            )
            if late_pue_invoices:
                raise ValidationError(_(
                    "PUE invoices can only be paid no later than the last day of their "
                    "invoice month. Reissue them as PPD before registering this payment: "
                    "%(documents)s",
                    documents=", ".join(late_pue_invoices.mapped("display_name")),
                ))

            different_payment_way_pue_invoices = pue_lines.invoice_id.filtered(
                lambda invoice: any(
                    self.currency_id.compare_amounts(
                        self.line_ids.filtered(
                            lambda line: line.invoice_id == invoice
                        )[self._get_component_allocation_field(component["sequence"])],
                        0.0,
                    ) > 0
                    and component["sat_payment_method"]
                    != invoice.l10n_mx_edi_payment_method_id
                    for component in components
                )
            )
            if different_payment_way_pue_invoices:
                raise ValidationError(_(
                    "The SAT payment way must match the payment way stamped on every PUE "
                    "invoice. Documents with a different way: %(documents)s",
                    documents=", ".join(different_payment_way_pue_invoices.mapped("display_name")),
                ))

            ppd_invoices = invoices.filtered(
                lambda invoice: invoice.l10n_mx_edi_payment_policy == "PPD"
            )
            if ppd_invoices:
                customer_rfcs = set(filter(
                    None,
                    ppd_invoices.mapped("l10n_mx_edi_cfdi_customer_rfc"),
                ))
                if len(customer_rfcs) != 1:
                    raise ValidationError(_(
                        "All PPD invoices must have the same customer RFC for the "
                        "payment complement."
                    ))
        return distribution_lines, components

    def _prepare_write_off_line_vals(self, excess_amount):
        self.ensure_one()
        if (
            self.currency_id.is_zero(excess_amount)
            or self.payment_difference_handling != "reconcile"
        ):
            return []
        amount_currency = -excess_amount
        return [{
            "name": self.writeoff_label.strip(),
            "account_id": self.writeoff_account_id.id,
            "partner_id": self.partner_id.commercial_partner_id.id,
            "currency_id": self.currency_id.id,
            "amount_currency": amount_currency,
            "balance": self.currency_id._convert(
                amount_currency,
                self.company_id.currency_id,
                self.company_id,
                self.payment_date,
            ),
        }]

    def _build_component_allocation_plan(self, distribution_lines, components):
        """Build the exact user-reviewed allocation for every payment method."""
        self.ensure_one()
        plans = []
        for component in components:
            allocation_field = self._get_component_allocation_field(
                component["sequence"]
            )
            allocations = [
                (line, line[allocation_field])
                for line in distribution_lines.sorted("sequence")
                if self.currency_id.compare_amounts(
                    line[allocation_field],
                    0.0,
                ) > 0
            ]
            allocated = sum(amount for _line, amount in allocations)
            plans.append({
                **component,
                "allocations": allocations,
                "excess_amount": max(component["amount"] - allocated, 0.0),
            })
        return plans

    def _split_payment_counterpart_lines(
        self,
        payment,
        allocations,
        open_excess_amount=0.0,
        has_writeoff=False,
    ):
        liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
        expected_writeoff_count = int(has_writeoff)
        if (
            len(liquidity_lines) != 1
            or len(counterpart_lines) != 1
            or len(writeoff_lines) != expected_writeoff_count
        ):
            raise UserError(_("Odoo did not generate the expected payment entry; the distribution was cancelled."))

        if not allocations:
            return self.env["account.move.line"]

        counterpart = counterpart_lines
        commands = []
        distributed_balance = 0.0
        distributed_amount_currency = 0.0
        has_open_excess = not self.currency_id.is_zero(open_excess_amount)
        allocation_target = sum(amount for _line, amount in allocations)
        if has_open_excess:
            target_amount_currency = -allocation_target
            target_balance = self.currency_id._convert(
                target_amount_currency,
                self.company_id.currency_id,
                self.company_id,
                self.payment_date,
            )
        else:
            # Preserve Odoo's exact counterpart values. Recomputing the
            # company-currency balance can introduce a one-cent difference
            # between the payment, allocations and write-off in foreign
            # currencies.
            target_amount_currency = counterpart.amount_currency
            target_balance = counterpart.balance
        for index, (line, allocation_amount) in enumerate(allocations):
            is_last = index == len(allocations) - 1
            if is_last:
                line_balance = target_balance - distributed_balance
                line_amount_currency = target_amount_currency - distributed_amount_currency
            else:
                line_amount_currency = -allocation_amount
                line_balance = self.currency_id._convert(
                    line_amount_currency,
                    self.company_id.currency_id,
                    self.company_id,
                    self.payment_date,
                )
            values = {
                "name": line.invoice_id.name,
                "date_maturity": self.payment_date,
                "partner_id": payment.partner_id.id,
                "account_id": line.account_id.id,
                "currency_id": self.currency_id.id,
                "balance": line_balance,
                "amount_currency": line_amount_currency,
            }
            commands.append(
                Command.update(counterpart.id, values)
                if index == 0
                else Command.create(values)
            )
            distributed_balance += line_balance
            distributed_amount_currency += line_amount_currency

        if has_open_excess:
            commands.append(Command.create({
                "name": _("Customer credit - unapplied payment excess"),
                "date_maturity": self.payment_date,
                "partner_id": payment.partner_id.id,
                "account_id": allocations[0][0].account_id.id,
                "currency_id": self.currency_id.id,
                "balance": counterpart.balance - target_balance,
                "amount_currency": counterpart.amount_currency - target_amount_currency,
            }))

        original_line_ids = set(payment.move_id.line_ids.ids)
        payment.move_id.line_ids = commands
        _liquidity_lines, split_counterpart_lines, _writeoff_lines = payment._seek_for_lines()
        expected_counterpart_count = len(allocations) + int(has_open_excess)
        if len(split_counterpart_lines) != expected_counterpart_count:
            raise UserError(_("The payment entry could not be split into the requested allocations."))
        new_counterpart_lines = split_counterpart_lines.filtered(
            lambda line: line.id not in original_line_ids
        ).sorted("id")
        payment_lines = counterpart | new_counterpart_lines[:len(allocations) - 1]
        if len(payment_lines) != len(allocations):
            raise UserError(_("The payment entry could not be split into the requested allocations."))
        return payment_lines

    def action_create_distributed_payment(self):
        self.ensure_one()
        distribution_lines, components = self._validate_before_payment()
        distribution_lines = distribution_lines.sorted("sequence")
        shortfall_by_invoice_id = {
            line.invoice_id.id: line.shortfall_amount
            for line in distribution_lines.filtered("forgive_shortfall")
        }
        plans = self._build_component_allocation_plan(
            distribution_lines,
            components,
        )
        receivable_account = distribution_lines.account_id
        payments = self.env["account.payment"]
        payment_plan_pairs = []
        for plan in plans:
            write_off_line_vals = self._prepare_write_off_line_vals(
                plan["excess_amount"]
            )
            payment = self.env["account.payment"].create({
                "amount": plan["amount"],
                "date": self.payment_date,
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": self.partner_id.commercial_partner_id.id,
                "distributed_payer_id": self.payer_id.commercial_partner_id.id,
                "distributed_payer_bank_id": plan["payer_bank"].id,
                "journal_id": plan["journal"].id,
                "company_id": self.company_id.id,
                "currency_id": self.currency_id.id,
                "payment_method_line_id": plan["payment_method_line"].id,
                "partner_bank_id": plan["journal"].bank_account_id.id,
                "destination_account_id": receivable_account.id,
                "memo": plan["memo"] or (
                    _("Distributed remission payment")
                    if self.document_kind == "remission"
                    else _("Distributed customer payment")
                ),
                "policy_observation": (self.policy_observation or "").strip() or False,
                "l10n_mx_edi_payment_method_id": (
                    plan["sat_payment_method"].id
                    if self.document_kind == "invoice"
                    else False
                ),
                "l10n_mx_edi_cfdi_origin": (
                    self.l10n_mx_edi_cfdi_origin
                    if self.document_kind == "invoice"
                    else False
                ),
                "multiple_invoice_distribution": True,
                "distributed_document_kind": self.document_kind,
                "distributed_payment_component_sequence": plan["sequence"],
                "distributed_sat_payment_method_id": (
                    plan["sat_payment_method"].id
                    if self.document_kind == "invoice"
                    else False
                ),
                "distributed_excess_amount": plan["excess_amount"],
                "distributed_difference_handling": (
                    self.payment_difference_handling
                    if not self.currency_id.is_zero(plan["excess_amount"])
                    else False
                ),
                "distributed_writeoff_account_id": (
                    self.writeoff_account_id.id if write_off_line_vals else False
                ),
                "distributed_writeoff_label": (
                    self.writeoff_label.strip() if write_off_line_vals else False
                ),
                "write_off_line_vals": write_off_line_vals,
            })
            payments |= payment
            payment_plan_pairs.append((payment, plan, bool(write_off_line_vals)))

        primary_payment = payments[:1]
        if len(payments) > 1:
            for payment in payments:
                payment.distributed_payment_primary_id = primary_payment

        message_items = []
        shortfall_recorded_invoice_ids = set()
        for payment, plan, has_writeoff in payment_plan_pairs:
            open_excess_amount = (
                plan["excess_amount"]
                if self.payment_difference_handling == "open"
                else 0.0
            )
            payment_lines = self._split_payment_counterpart_lines(
                payment,
                plan["allocations"],
                open_excess_amount=open_excess_amount,
                has_writeoff=has_writeoff,
            )
            payment.with_context(multiple_payment_initial_post=True).action_post()
            distribution_values = []
            for sequence, ((wizard_line, allocation_amount), payment_line) in enumerate(
                zip(plan["allocations"], payment_lines), start=1
            ):
                invoice = wizard_line.invoice_id
                previous_residual = invoice.amount_residual
                invoice_lines = self._get_open_receivable_lines(invoice).filtered(
                    lambda line: line.account_id == payment_line.account_id
                )
                if not invoice_lines:
                    raise UserError(_(
                        "Invoice %(invoice)s has no open receivable line.",
                        invoice=invoice.display_name,
                    ))
                (payment_line + invoice_lines).reconcile()
                invoice.invalidate_recordset(["amount_residual", "payment_state"])
                partials = (
                    payment_line.matched_debit_ids
                    | payment_line.matched_credit_ids
                ).filtered(lambda partial: (
                    partial.debit_move_id.move_id == invoice
                    or partial.credit_move_id.move_id == invoice
                ))
                distribution_values.append({
                    "sequence": sequence,
                    "payment_id": payment.id,
                    "invoice_id": invoice.id,
                    "currency_id": self.currency_id.id,
                    "amount": allocation_amount,
                    "previous_residual": previous_residual,
                    "remaining_residual": invoice.amount_residual,
                    "partial_reconcile_ids": [Command.set(partials.ids)],
                    "payment_move_line_id": payment_line.id,
                    "shortfall_amount": (
                        shortfall_by_invoice_id.get(invoice.id, 0.0)
                        if invoice.id in shortfall_by_invoice_id
                        and invoice.id not in shortfall_recorded_invoice_ids
                        else 0.0
                    ),
                    "shortfall_account_id": (
                        self.shortfall_account_id.id
                        if invoice.id in shortfall_by_invoice_id
                        and invoice.id not in shortfall_recorded_invoice_ids
                        else False
                    ),
                    "shortfall_journal_id": (
                        self.shortfall_journal_id.id
                        if invoice.id in shortfall_by_invoice_id
                        and invoice.id not in shortfall_recorded_invoice_ids
                        else False
                    ),
                })
                if invoice.id in shortfall_by_invoice_id:
                    shortfall_recorded_invoice_ids.add(invoice.id)
                message_items.append(Markup("<li>%s - %s: %s</li>") % (
                    plan["sat_payment_method"].display_name
                    if plan["sat_payment_method"]
                    else plan["journal"].display_name,
                    invoice._get_html_link(),
                    format_amount(self.env, allocation_amount, self.currency_id),
                ))
            if distribution_values:
                self.env["account.payment.invoice.distribution"].create(
                    distribution_values
                )

        payments._restore_distributed_shortfall_adjustments()

        primary_payment.message_post(
            body=Markup("<p>%s</p><ul>%s</ul>") % (
                _("Payment distributed from the multiple-invoice wizard."),
                Markup().join(message_items),
            )
        )
        return primary_payment.action_open_business_doc()


class MultipleInvoicePaymentWizardLine(models.TransientModel):
    _name = "multiple.invoice.payment.wizard.line"
    _description = "Invoice allocation line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    wizard_id = fields.Many2one(
        comodel_name="multiple.invoice.payment.wizard",
        required=True,
        ondelete="cascade",
    )
    invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="Invoice",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        required=True,
        readonly=True,
    )
    currency_id = fields.Many2one(related="invoice_id.currency_id")
    invoice_date = fields.Date(related="invoice_id.invoice_date", string="Invoice date")
    invoice_date_due = fields.Date(related="invoice_id.invoice_date_due", string="Due date")
    payment_policy = fields.Selection(
        related="invoice_id.l10n_mx_edi_payment_policy",
        string="Policy",
    )
    cfdi_uuid = fields.Char(related="invoice_id.l10n_mx_edi_cfdi_uuid", string="CFDI UUID")
    amount_total = fields.Monetary(
        related="invoice_id.amount_total",
        string="Total",
        currency_field="currency_id",
    )
    initial_residual = fields.Monetary(
        string="Initial outstanding",
        currency_field="currency_id",
        readonly=True,
    )
    current_residual = fields.Monetary(
        related="invoice_id.amount_residual",
        string="Current outstanding",
        currency_field="currency_id",
    )
    allocation_amount = fields.Monetary(
        string="Aplicar con método 1",
        currency_field="currency_id",
    )
    second_allocation_amount = fields.Monetary(
        string="Aplicar con método 2",
        currency_field="currency_id",
    )
    third_allocation_amount = fields.Monetary(
        string="Aplicar con método 3",
        currency_field="currency_id",
    )
    total_allocation_amount = fields.Monetary(
        string="Total aplicado",
        currency_field="currency_id",
        compute="_compute_remaining_residual",
    )
    remaining_after_first = fields.Monetary(
        string="Disponible después del método 1",
        currency_field="currency_id",
        compute="_compute_remaining_residual",
    )
    remaining_after_second = fields.Monetary(
        string="Disponible después del método 2",
        currency_field="currency_id",
        compute="_compute_remaining_residual",
    )
    remaining_residual = fields.Monetary(
        string="Remaining",
        currency_field="currency_id",
        compute="_compute_remaining_residual",
    )
    forgive_shortfall = fields.Boolean(
        string="Perdonar centavos faltantes",
        help="Cierra el saldo menor a 1.00 con un asiento de gasto separado del cobro real.",
    )
    shortfall_amount = fields.Monetary(
        string="Faltante a gasto",
        currency_field="currency_id",
        compute="_compute_remaining_residual",
    )

    @api.depends(
        "current_residual",
        "allocation_amount",
        "second_allocation_amount",
        "third_allocation_amount",
        "forgive_shortfall",
    )
    def _compute_remaining_residual(self):
        for line in self:
            line.total_allocation_amount = (
                line.allocation_amount
                + line.second_allocation_amount
                + line.third_allocation_amount
            )
            line.remaining_after_first = (
                line.current_residual - line.allocation_amount
            )
            line.remaining_after_second = (
                line.remaining_after_first - line.second_allocation_amount
            )
            line.remaining_residual = (
                line.remaining_after_second - line.third_allocation_amount
            )
            line.shortfall_amount = (
                max(line.remaining_residual, 0.0)
                if line.forgive_shortfall else 0.0
            )
