from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class AccountPayment(models.Model):
    _inherit = "account.payment"

    policy_observation = fields.Char(
        string="Observación",
        help="Observación operativa de la póliza; no modifica el CFDI ni el asiento contable.",
    )
    policy_period = fields.Char(
        string="Periodo de póliza",
        copy=False,
        readonly=True,
        index=True,
    )
    policy_sequence = fields.Integer(
        string="Consecutivo de póliza",
        copy=False,
        readonly=True,
        index=True,
    )
    policy_number = fields.Char(
        string="Póliza",
        compute="_compute_policy_number",
    )
    receipt_sequence = fields.Integer(
        string="Consecutivo RP",
        copy=False,
        readonly=True,
        index=True,
    )
    receipt_number = fields.Char(
        string="Folio RP",
        compute="_compute_receipt_number",
        store=True,
        index=True,
    )
    multiple_invoice_distribution = fields.Boolean(
        string="Distributed over multiple invoices",
        copy=False,
        readonly=True,
    )
    distributed_document_kind = fields.Selection(
        selection=[
            ("invoice", "Facturas"),
            ("remission", "Remisiones"),
        ],
        string="Tipo de documento",
        default="invoice",
        readonly=True,
        copy=False,
    )
    distributed_payer_id = fields.Many2one(
        comodel_name="res.partner",
        string="Payer",
        readonly=True,
        copy=False,
        check_company=True,
        help=(
            "Third party that provided the funds. The customer remains the "
            "accounting debtor and CFDI receiver."
        ),
    )
    distributed_payer_bank_id = fields.Many2one(
        comodel_name="res.partner.bank",
        string="Payer bank account",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
        help=(
            "Explicit ordering account used for the payment receipt and, "
            "when applicable, the REP."
        ),
    )
    invoice_distribution_ids = fields.One2many(
        comodel_name="account.payment.invoice.distribution",
        inverse_name="payment_id",
        string="Invoice distribution",
        readonly=True,
        copy=False,
    )
    distributed_excess_amount = fields.Monetary(
        string="Importe no aplicado",
        currency_field="currency_id",
        readonly=True,
        copy=False,
    )
    distributed_difference_handling = fields.Selection(
        selection=[
            ("open", "Saldo a favor del cliente"),
            ("reconcile", "Cuenta de diferencia"),
        ],
        string="Destino del importe no aplicado",
        readonly=True,
        copy=False,
    )
    distributed_writeoff_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Difference account",
        readonly=True,
        copy=False,
        check_company=True,
    )
    distributed_writeoff_label = fields.Char(
        string="Difference label",
        readonly=True,
        copy=False,
    )
    distributed_payment_primary_id = fields.Many2one(
        comodel_name="account.payment",
        string="Pago principal",
        readonly=True,
        copy=False,
        index=True,
        ondelete="set null",
        help="Vincula los pagos contables que integran un cobro con varias formas de pago.",
    )
    distributed_payment_component_sequence = fields.Integer(
        string="Secuencia de forma de pago",
        readonly=True,
        copy=False,
        default=1,
    )
    distributed_sat_payment_method_id = fields.Many2one(
        comodel_name="l10n_mx_edi.payment.method",
        string="Forma de pago SAT original",
        readonly=True,
        copy=False,
        help=(
            "Conserva la forma de pago seleccionada para restaurarla si el "
            "cobro agrupado se cancela y posteriormente se vuelve a publicar."
        ),
    )
    distributed_group_payment_ids = fields.Many2many(
        comodel_name="account.payment",
        string="Formas de pago",
        compute="_compute_distributed_group_payment_ids",
    )
    distributed_group_distribution_ids = fields.Many2many(
        comodel_name="account.payment.invoice.distribution",
        string="Distribución por forma de pago",
        compute="_compute_distributed_group_payment_ids",
    )
    distributed_group_total_amount = fields.Monetary(
        string="Total cobrado",
        currency_field="currency_id",
        compute="_compute_distributed_group_payment_ids",
    )
    distributed_is_group_primary = fields.Boolean(
        string="Es el pago principal",
        compute="_compute_distributed_group_payment_ids",
    )

    @api.depends("policy_sequence", "distributed_payment_primary_id.policy_sequence")
    def _compute_policy_number(self):
        for payment in self:
            primary = payment.distributed_payment_primary_id or payment
            payment.policy_number = (
                "%05d" % primary.policy_sequence
                if primary.policy_sequence else False
            )

    @api.depends("receipt_sequence", "distributed_payment_primary_id.receipt_sequence")
    def _compute_receipt_number(self):
        for payment in self:
            primary = payment.distributed_payment_primary_id or payment
            payment.receipt_number = (
                "RP/%05d" % primary.receipt_sequence
                if primary.receipt_sequence else False
            )

    @api.constrains("date", "policy_period", "policy_sequence")
    def _check_policy_period_matches_payment_date(self):
        for payment in self:
            if (
                payment.policy_sequence
                and payment.date
                and payment.policy_period
                != fields.Date.to_date(payment.date).strftime("%Y-%m")
            ):
                raise ValidationError(_(
                    "El pago ya tiene una póliza de otro mes. Conserva la fecha "
                    "dentro del mes de la póliza o registra un nuevo pago."
                ))

    @api.onchange("journal_id", "date")
    def _onchange_policy_observation(self):
        for payment in self:
            if not payment.policy_observation and payment.journal_id and payment.date:
                code = (payment.journal_id.code or "").strip().upper()
                if code:
                    payment.policy_observation = "%s %s" % (
                        code,
                        fields.Date.to_date(payment.date).strftime("%d%m%y"),
                    )

    def _assign_policy_numbers(self):
        """Allocate one company-wide number per posted collection and month.

        The company row lock serializes concurrent posts, including posts from
        distinct journals.  Component payments share the primary's number.
        Existing numbers survive cancellation/reposting and are never reused.
        """
        for payment in self.sorted(lambda record: record.id):
            if (
                payment.policy_sequence
                or payment.state in ("draft", "canceled")
                or (
                    payment.distributed_payment_primary_id
                    and payment.distributed_payment_primary_id != payment
                )
            ):
                continue
            period = fields.Date.to_date(payment.date).strftime("%Y-%m")
            self.env.cr.execute(
                "SELECT id FROM res_company WHERE id = %s FOR UPDATE",
                [payment.company_id.id],
            )
            self.env.cr.execute(
                """SELECT COALESCE(MAX(policy_sequence), 0)
                   FROM account_payment
                   WHERE company_id = %s AND policy_period = %s""",
                [payment.company_id.id, period],
            )
            next_number = self.env.cr.fetchone()[0] + 1
            payment.write({
                "policy_period": period,
                "policy_sequence": next_number,
            })

    def _assign_receipt_numbers(self):
        """Number signed PPD payment complements after the company's RP anchor.

        A company lock serializes stamping across bank journals. Group
        components expose their primary's RP number and never consume one.
        Cancellation/reposting does not release a number.
        """
        for payment in self.sorted(lambda record: record.id):
            if (
                payment.receipt_sequence
                or not payment._has_signed_ppd_complement()
            ):
                continue
            self.env.cr.execute(
                "SELECT id FROM res_company WHERE id = %s FOR UPDATE",
                [payment.company_id.id],
            )
            anchor = self.search([
                ("company_id", "=", payment.company_id.id),
                ("receipt_sequence", "=", 1),
            ], limit=1)
            if not anchor or payment.id < anchor.id:
                continue
            self.env.cr.execute(
                """SELECT COALESCE(MAX(receipt_sequence), 0)
                   FROM account_payment WHERE company_id = %s""",
                [payment.company_id.id],
            )
            payment.receipt_sequence = self.env.cr.fetchone()[0] + 1

    def _has_signed_ppd_complement(self):
        """Only an actual signed REP for a PPD invoice can consume an RP folio."""
        self.ensure_one()
        if (
            self.state in ("draft", "canceled")
            or self.payment_type != "inbound"
            or self.partner_type != "customer"
            or self.distributed_document_kind == "remission"
            or (
                self.distributed_payment_primary_id
                and self.distributed_payment_primary_id != self
            )
        ):
            return False
        return any(
            document.state == "payment_sent"
            and document.attachment_id
            and document.attachment_uuid
            and any(
                invoice.l10n_mx_edi_payment_policy == "PPD"
                for invoice in document.invoice_ids
            )
            for document in self.l10n_mx_edi_payment_document_ids
        )

    def action_start_receipt_series(self):
        """Start the internal RP series at the agreed historical payment."""
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise UserError(_("Solo un responsable de contabilidad puede iniciar la serie RP."))
        if (
            self.name != "PBC/2026/00014"
            or self.payment_type != "inbound"
            or self.partner_type != "customer"
            or self.state in ("draft", "canceled")
            or (
                self.distributed_payment_primary_id
                and self.distributed_payment_primary_id != self
            )
            or not self._has_signed_ppd_complement()
        ):
            raise UserError(_(
                "La serie RP debe comenzar en el cobro PPD PBC/2026/00014 "
                "después de timbrar su complemento de pago."
            ))
        self._start_receipt_series()
        return True

    def _start_receipt_series(self):
        """Assign RP/00001 here and backfill later signed PPD REPs by ID."""
        self.ensure_one()
        if not self._has_signed_ppd_complement():
            raise UserError(_(
                "Primero timbra el complemento de pago PPD para iniciar la serie RP."
            ))
        self.env.cr.execute(
            "SELECT id FROM res_company WHERE id = %s FOR UPDATE",
            [self.company_id.id],
        )
        existing = self.search([
            ("company_id", "=", self.company_id.id),
            ("receipt_sequence", ">", 0),
        ], limit=1)
        if existing:
            raise UserError(_("La serie RP ya fue iniciada en esta empresa."))
        self.receipt_sequence = 1
        later_payments = self.search([
            ("company_id", "=", self.company_id.id),
            ("id", ">", self.id),
            ("payment_type", "=", "inbound"),
            ("partner_type", "=", "customer"),
            ("state", "not in", ("draft", "canceled")),
        ], order="id")
        later_payments._assign_receipt_numbers()

    def action_print_policy(self):
        self.ensure_one()
        if not self.policy_number:
            raise UserError(_("Publica el pago antes de imprimir su póliza."))
        return self.env.ref(
            "advances_on_multiple_invoices.action_report_payment_policy"
        ).report_action(self.distributed_payment_primary_id or self)

    def _get_distributed_payment_components(self):
        """Return the ordered accounting payments in the same collection."""
        self.ensure_one()
        primary = self.distributed_payment_primary_id
        if not primary:
            return self
        return self.search([
            ("distributed_payment_primary_id", "=", primary.id),
        ]).sorted(lambda payment: (
            payment.distributed_payment_component_sequence,
            payment.id,
        ))

    def _expand_distributed_payment_groups(self):
        """Return all accounting payments in each selected collection."""
        payments = self
        for payment in self:
            if payment.distributed_payment_primary_id:
                payments |= payment._get_distributed_payment_components()
        return payments

    def action_cancel(self):
        payments = self._expand_distributed_payment_groups()
        payments._cancel_distributed_shortfall_adjustments()
        return super(AccountPayment, payments).action_cancel()

    def action_draft(self):
        payments = self._expand_distributed_payment_groups()
        payments._cancel_distributed_shortfall_adjustments()
        return super(AccountPayment, payments).action_draft()

    def _cancel_distributed_shortfall_adjustments(self):
        distributions = self.invoice_distribution_ids.filtered("shortfall_move_id")
        for distribution in distributions:
            adjustment = distribution.shortfall_move_id
            if adjustment.state == "posted":
                adjustment.line_ids.filtered(
                    lambda line: line.account_id.account_type == "asset_receivable"
                ).remove_move_reconcile()
                adjustment.button_draft()
            if adjustment.state == "draft":
                adjustment.button_cancel()
            distribution.sudo().shortfall_move_id = False

    def _restore_distributed_shortfall_adjustments(self):
        """Close only the waived cents; never add them to the actual payment."""
        distributions = self.invoice_distribution_ids.filtered(
            lambda line: line.shortfall_amount and not line.shortfall_move_id
        )
        for distribution in distributions:
            invoice = distribution.invoice_id
            currency = distribution.currency_id
            shortfall = distribution.shortfall_amount
            invoice.invalidate_recordset(["amount_residual", "payment_state"])
            if currency.compare_amounts(invoice.amount_residual, shortfall) != 0:
                raise UserError(_(
                    "The remaining balance on %(invoice)s is no longer %(amount)s. "
                    "The centavo adjustment was not posted.",
                    invoice=invoice.display_name,
                    amount=shortfall,
                ))
            account = distribution.shortfall_account_id
            journal = distribution.shortfall_journal_id
            if (
                not account or account.account_type != "expense"
                or not journal or journal.type != "general"
                or invoice.company_id not in account.company_ids
                or journal.company_id != invoice.company_id
            ):
                raise UserError(_("The shortfall account or journal is no longer valid."))
            receivable_lines = invoice.line_ids.filtered(
                lambda line: line.account_id.account_type == "asset_receivable"
                and not line.reconciled
            )
            if len(receivable_lines.account_id) != 1:
                raise UserError(_("The invoice no longer has one open receivable account."))
            label = _("Forgiven collection difference: %(invoice)s", invoice=invoice.name)
            adjustment = self.env["account.move"].create({
                "move_type": "entry",
                "journal_id": journal.id,
                "company_id": invoice.company_id.id,
                "date": distribution.payment_id.date,
                "ref": label,
                "line_ids": [
                    Command.create({
                        "name": label,
                        "account_id": account.id,
                        "partner_id": invoice.commercial_partner_id.id,
                        "debit": shortfall,
                        "credit": 0.0,
                    }),
                    Command.create({
                        "name": label,
                        "account_id": receivable_lines.account_id.id,
                        "partner_id": invoice.commercial_partner_id.id,
                        "debit": 0.0,
                        "credit": shortfall,
                    }),
                ],
            })
            adjustment.action_post()
            adjustment_receivable = adjustment.line_ids.filtered(
                lambda line: line.account_id == receivable_lines.account_id
            )
            (receivable_lines + adjustment_receivable).reconcile()
            distribution.sudo().shortfall_move_id = adjustment

    def button_request_cancel(self):
        self.ensure_one()
        payment = self.distributed_payment_primary_id or self
        return super(AccountPayment, payment).button_request_cancel()

    def action_cancel_distributed_group(self):
        """Cancel the accounting group or request cancellation of its REP.

        A multi-method collection has a single fiscal document on the primary
        payment.  The accounting cancellation, however, must always affect
        every component in the group.
        """
        self.ensure_one()
        payment = self.distributed_payment_primary_id or self
        if payment.move_id.need_cancel_request:
            return payment.button_request_cancel()
        return payment.action_cancel()

    def action_post(self):
        """Post a complete existing group and restore its allocations.

        Initial creation posts components one at a time while their journal
        items are still being split.  Later reposts are group operations and
        must recreate the partial reconciliations removed by cancellation.
        """
        payments = self
        if not self.env.context.get("multiple_payment_initial_post"):
            payments = self._expand_distributed_payment_groups()
            payments._restore_distributed_sat_payment_methods()
        result = super(AccountPayment, payments).action_post()
        payments._assign_policy_numbers()
        if not self.env.context.get("multiple_payment_initial_post"):
            payments._restore_distributed_reconciliations()
            payments._restore_distributed_shortfall_adjustments()
        return result

    def _restore_distributed_sat_payment_methods(self):
        for payment in self.filtered(
            lambda pay: (
                pay.distributed_sat_payment_method_id
                and not pay.l10n_mx_edi_payment_method_id
            )
        ):
            payment.l10n_mx_edi_payment_method_id = (
                payment.distributed_sat_payment_method_id
            )

    def _restore_distributed_reconciliations(self):
        """Recreate invoice reconciliations after a cancelled group is reposted."""
        payments = self.filtered("multiple_invoice_distribution").sorted(
            lambda payment: (
                payment.distributed_payment_component_sequence,
                payment.id,
            )
        )
        for payment in payments:
            distributions = payment.invoice_distribution_ids.sorted(
                lambda distribution: (distribution.sequence, distribution.id)
            )
            if not distributions:
                continue

            _liquidity_lines, counterpart_lines, _writeoff_lines = (
                payment._seek_for_lines()
            )
            available_lines = counterpart_lines.filtered(
                lambda line: not line.reconciled
            ).sorted("id")
            used_line_ids = set()

            for distribution in distributions:
                if distribution.partial_reconcile_ids:
                    continue

                payment_line = distribution.payment_move_line_id
                if (
                    not payment_line
                    or payment_line.move_id != payment.move_id
                    or payment_line.reconciled
                    or payment_line.id in used_line_ids
                ):
                    payment_line = self._find_distribution_payment_line(
                        distribution,
                        available_lines.filtered(
                            lambda line: line.id not in used_line_ids
                        ),
                    )
                if not payment_line:
                    raise UserError(_(
                        "The accounting line for %(invoice)s could not be found. "
                        "The grouped payment was not reposted.",
                        invoice=distribution.invoice_id.display_name,
                    ))

                invoice = distribution.invoice_id
                invoice_lines = invoice.line_ids.filtered(
                    lambda line: (
                        line.account_id == payment_line.account_id
                        and not line.reconciled
                    )
                )
                if not invoice_lines:
                    raise UserError(_(
                        "Invoice %(invoice)s no longer has an open receivable "
                        "line for this grouped payment.",
                        invoice=invoice.display_name,
                    ))

                previous_residual = invoice.amount_residual
                (payment_line + invoice_lines).reconcile()
                invoice.invalidate_recordset(["amount_residual", "payment_state"])
                partials = (
                    payment_line.matched_debit_ids
                    | payment_line.matched_credit_ids
                ).filtered(lambda partial: (
                    partial.debit_move_id.move_id == invoice
                    or partial.credit_move_id.move_id == invoice
                ))
                if not partials:
                    raise UserError(_(
                        "The allocation for %(invoice)s could not be restored.",
                        invoice=invoice.display_name,
                    ))
                # Distribution records are intentionally read-only for users;
                # this controlled lifecycle update is performed by the model.
                distribution.sudo().write({
                    "payment_move_line_id": payment_line.id,
                    "partial_reconcile_ids": [Command.set(partials.ids)],
                    "previous_residual": previous_residual,
                    "remaining_residual": invoice.amount_residual,
                })
                used_line_ids.add(payment_line.id)

    def _find_distribution_payment_line(self, distribution, available_lines):
        """Find the legacy counterpart line for a distribution without a link."""
        currency = distribution.currency_id
        matching_lines = available_lines.filtered(lambda line: (
            line.currency_id == currency
            and currency.compare_amounts(
                abs(line.amount_currency),
                distribution.amount,
            ) == 0
        ))
        return (matching_lines or available_lines)[:1]

    @api.depends(
        "distributed_payment_primary_id",
        "amount",
        "invoice_distribution_ids",
    )
    def _compute_distributed_group_payment_ids(self):
        for payment in self:
            components = payment._get_distributed_payment_components()
            payment.distributed_group_payment_ids = components
            payment.distributed_group_distribution_ids = (
                components.invoice_distribution_ids
            )
            payment.distributed_group_total_amount = sum(components.mapped("amount"))
            payment.distributed_is_group_primary = bool(
                payment.distributed_payment_primary_id == payment
            )

    @api.constrains(
        "distributed_payer_id",
        "distributed_payer_bank_id",
        "company_id",
    )
    def _check_distributed_payer_bank_id(self):
        for payment in self.filtered("distributed_payer_bank_id"):
            payer = payment.distributed_payer_id.commercial_partner_id
            bank_owner = (
                payment.distributed_payer_bank_id.partner_id.commercial_partner_id
            )
            if not payer or bank_owner != payer:
                raise ValidationError(_(
                    "The ordering bank account must belong to the selected payer."
                ))
            if (
                payment.distributed_payer_bank_id.company_id
                and payment.distributed_payer_bank_id.company_id != payment.company_id
            ):
                raise ValidationError(_(
                    "The ordering bank account must be available to the payment company."
                ))

    def _get_receipt_payer_address_extras(self, partner):
        """Read optional MX/Studio address fields without requiring either addon."""
        self.ensure_one()

        def optional_value(label, preferred_names):
            studio_names = sorted(
                name for name, field in partner._fields.items()
                if name.startswith("x_")
                and (label in name.casefold() or label in (field.string or "").casefold())
                and not any(word in name.casefold() for word in ("code", "codigo"))
            )
            for name in dict.fromkeys((*preferred_names, *studio_names)):
                field = partner._fields.get(name)
                if not field or field.type not in ("char", "text", "selection", "many2one"):
                    continue
                value = partner[name]
                if value:
                    return value.display_name if field.type == "many2one" else str(value)
            return False

        return {
            "colony": optional_value("colonia", ("l10n_mx_edi_colony", "x_studio_colonia", "x_colonia")),
            "locality": optional_value("localidad", ("l10n_mx_edi_locality", "x_studio_localidad", "x_localidad")),
            "municipality": optional_value("municipio", ("x_studio_municipio", "x_municipio")) or partner.city,
        }

    def _get_multiple_invoice_payment_bank_report_values(self, cfdi_values=None):
        """Return stamped emitter data and the payment journal receiver data."""
        self.ensure_one()
        cfdi_values = cfdi_values or {}
        payment_info = cfdi_values.get("payment_info") or {}

        payer = (self.distributed_payer_id or self.partner_id).commercial_partner_id
        payer_bank = self.distributed_payer_bank_id
        receiver_bank = self.partner_bank_id or self.journal_id.bank_account_id

        is_remission = self.distributed_document_kind == "remission"
        receiver_account_holder = (
            receiver_bank.acc_holder_name
            or receiver_bank.partner_id.display_name
            or self.journal_id.display_name
        )

        return {
            "emitter_name": payer.display_name,
            # Prefer certified information. The fallback is safe because this
            # account was explicitly selected for this payment, not guessed
            # from the payer's master data.
            "emitter_account": (
                payment_info.get("from_account_number")
                or payer_bank.acc_number
                or False
            ),
            "emitter_bank_name": (
                payment_info.get("from_account_name")
                or payer_bank.bank_id.name
                or False
            ),
            "receiver_name": (
                receiver_account_holder
                if is_remission
                else self.company_id.partner_id.display_name
            ),
            "receiver_company_name": self.company_id.partner_id.display_name,
            "receiver_account": (
                payment_info.get("to_account_number")
                or receiver_bank.acc_number
                or False
            ),
            "receiver_bank_name": (
                payment_info.get("to_account_name")
                or receiver_bank.bank_id.name
                or False
            ),
        }

    def _get_multiple_invoice_payment_cfdi_bank_values(self, cfdi_node):
        """Match this component to its signed Pago node before reading accounts."""
        self.ensure_one()
        if cfdi_node is None or self.distributed_document_kind == "remission":
            return {}
        payment_nodes = cfdi_node.xpath('.//*[local-name()="Pago"]')
        if not payment_nodes:
            return {}

        matching_nodes = [
            node for node in payment_nodes
            if self.memo and node.get("NumOperacion") == self.memo
        ]
        if len(matching_nodes) == 1:
            payment_node = matching_nodes[0]
        else:
            components = self._get_distributed_payment_components()
            sequence = self.distributed_payment_component_sequence
            if len(payment_nodes) != len(components) or not 1 <= sequence <= len(payment_nodes):
                return {}
            payment_node = payment_nodes[sequence - 1]
            if (
                self.memo and payment_node.get("NumOperacion")
                and payment_node.get("NumOperacion") != self.memo
            ):
                return {}

        return {"payment_info": {
            "from_account_number": payment_node.get("CtaOrdenante"),
            "from_account_name": payment_node.get("NomBancoOrdExt"),
            "to_account_number": payment_node.get("CtaBeneficiario"),
            "to_account_name": payment_node.get("NomBancoBenExt"),
        }}


class AccountPaymentInvoiceDistribution(models.Model):
    _name = "account.payment.invoice.distribution"
    _description = "Payment distribution over an invoice"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    payment_id = fields.Many2one(
        comodel_name="account.payment",
        string="Payment",
        required=True,
        ondelete="cascade",
        index=True,
    )
    payment_component_sequence = fields.Integer(
        related="payment_id.distributed_payment_component_sequence",
        string="Método",
    )
    payment_journal_id = fields.Many2one(
        related="payment_id.journal_id",
        string="Diario",
    )
    sat_payment_method_id = fields.Many2one(
        related="payment_id.l10n_mx_edi_payment_method_id",
        string="Forma de pago SAT",
    )
    operation_number = fields.Char(
        related="payment_id.memo",
        string="Núm. de operación",
    )
    invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="Invoice",
        required=True,
        ondelete="restrict",
        index=True,
        domain="[('move_type', '=', 'out_invoice')]",
    )
    partner_id = fields.Many2one(related="payment_id.partner_id", store=True)
    company_id = fields.Many2one(related="payment_id.company_id", store=True)
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        required=True,
    )
    amount = fields.Monetary(
        string="Applied amount",
        currency_field="currency_id",
        required=True,
    )
    previous_residual = fields.Monetary(
        string="Previous balance",
        currency_field="currency_id",
        readonly=True,
    )
    remaining_residual = fields.Monetary(
        string="Remaining balance",
        currency_field="currency_id",
        readonly=True,
    )
    shortfall_amount = fields.Monetary(
        string="Faltante perdonado",
        currency_field="currency_id",
        readonly=True,
        copy=False,
    )
    shortfall_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Cuenta del faltante",
        readonly=True,
        copy=False,
        check_company=True,
    )
    shortfall_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Diario del faltante",
        readonly=True,
        copy=False,
        check_company=True,
    )
    shortfall_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Asiento del faltante",
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    partial_reconcile_ids = fields.Many2many(
        comodel_name="account.partial.reconcile",
        relation="account_payment_invoice_distribution_partial_rel",
        column1="distribution_id",
        column2="partial_reconcile_id",
        string="Partial reconciliations",
        readonly=True,
        copy=False,
    )
    payment_move_line_id = fields.Many2one(
        comodel_name="account.move.line",
        string="Línea contable del pago",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    reconciliation_active = fields.Boolean(
        string="Reconciliation active",
        compute="_compute_reconciliation_active",
    )

    @api.depends("partial_reconcile_ids")
    def _compute_reconciliation_active(self):
        for distribution in self:
            distribution.reconciliation_active = bool(distribution.partial_reconcile_ids)
