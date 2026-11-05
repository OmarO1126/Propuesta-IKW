import re

from odoo import api, fields, models


_REMISSION_FIELDS = (
    "is_remission",
    "x_studio_es_una_remision",
    "x_studio_es_una_remision_1",
)

_PAYMENT_TOTAL_KEYS = (
    "total_traslados_base_iva0",
    "total_traslados_impuesto_iva0",
    "total_traslados_base_iva_exento",
    "total_traslados_base_iva8",
    "total_traslados_impuesto_iva8",
    "total_traslados_base_iva16",
    "total_traslados_impuesto_iva16",
    "total_retenciones_isr",
    "total_retenciones_iva",
    "total_retenciones_ieps",
)

_PAYMENT_NODE_KEYS = (
    "fecha_pago",
    "forma_de_pago",
    "moneda",
    "tipo_cambio",
    "tipo_cambio_dp",
    "monto",
    "num_operacion",
    "rfc_emisor_cta_ord",
    "nom_banco_ord_ext",
    "cta_ordenante",
    "rfc_emisor_cta_ben",
    "cta_beneficiario",
    "docto_relationado_list",
    "retenciones_list",
    "traslados_list",
)


class AccountMove(models.Model):
    _inherit = "account.move"

    is_remission = fields.Boolean(
        string="Remisión desde venta",
        copy=False,
        index=True,
        help="Documento contable interno que no debe generar un CFDI.",
    )
    remission_issuer_company_id = fields.Many2one(
        comodel_name="res.company",
        string="Razón social de la remisión",
        copy=False,
        readonly=True,
    )
    remission_issuer_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contacto emisor de remisiones",
        copy=False,
        readonly=True,
    )

    def _multiple_invoice_expand_payment_group_moves(self):
        """Return all journal entries belonging to selected grouped payments."""
        moves = self
        for move in self:
            payment = move.origin_payment_id
            if payment.distributed_payment_primary_id:
                moves |= payment._get_distributed_payment_components().move_id
        return moves

    def button_draft(self):
        moves = self._multiple_invoice_expand_payment_group_moves()
        moves.origin_payment_id._cancel_distributed_shortfall_adjustments()
        return super(AccountMove, moves).button_draft()

    def button_cancel(self):
        moves = self._multiple_invoice_expand_payment_group_moves()
        moves.origin_payment_id._cancel_distributed_shortfall_adjustments()
        return super(AccountMove, moves).button_cancel()

    multiple_invoice_visible_cfdi_document_ids = fields.Many2many(
        comodel_name="l10n_mx_edi.document",
        string="Visible CFDI documents",
        compute="_compute_multiple_invoice_visible_cfdi_document_ids",
    )

    @api.depends(
        "l10n_mx_edi_payment_policy",
        "l10n_mx_edi_invoice_document_ids.state",
        "l10n_mx_edi_invoice_document_ids.sat_state",
        "l10n_mx_edi_payment_document_ids.state",
        "l10n_mx_edi_payment_document_ids.sat_state",
    )
    def _compute_multiple_invoice_visible_cfdi_document_ids(self):
        """Keep payment CFDIs out of the CFDI tab of PUE invoices.

        Odoo links a payment document to every reconciled invoice for its own
        update tracking, including PUE invoices that are deliberately omitted
        from the REP's DoctoRelacionado nodes.  Keep that internal relation and
        only filter the form's presentation so a PUE invoice does not appear to
        have a payment complement.
        """
        for move in self:
            documents = move.l10n_mx_edi_document_ids
            if move._multiple_invoice_is_remission():
                documents = documents.filtered(
                    lambda document: not document.state.startswith("payment_")
                )
            elif move.is_invoice() and move.l10n_mx_edi_payment_policy == "PUE":
                documents = documents.filtered(
                    lambda document: not document.state.startswith("payment_")
                )
            move.multiple_invoice_visible_cfdi_document_ids = documents

    def _multiple_invoice_is_remission(self):
        """Return whether the optional Studio marker identifies a remission.

        The legacy field is read as a fallback during the transition to the
        definitive technical name, so already-marked documents are not lost.
        """
        self.ensure_one()
        return any(
            field_name in self._fields and bool(self[field_name])
            for field_name in _REMISSION_FIELDS
        )

    def _l10n_mx_edi_is_cfdi_invoice(self):
        self.ensure_one()
        return (
            super()._l10n_mx_edi_is_cfdi_invoice()
            and not self._multiple_invoice_is_remission()
        )

    def _l10n_mx_edi_is_cfdi_payment(self):
        self.ensure_one()
        payment = self.origin_payment_id
        if not super()._l10n_mx_edi_is_cfdi_payment():
            return False
        if payment.distributed_document_kind == "remission":
            return False
        if (
            payment.distributed_payment_primary_id
            and not self.env.context.get("multiple_payment_include_components")
        ):
            return payment.distributed_payment_primary_id == payment
        return True

    def _l10n_mx_edi_cfdi_payment_document_sent(self, invoices, cfdi_filename, cfdi_str):
        """Allocate RP only once Odoo has stored the signed PPD payment CFDI."""
        document = super()._l10n_mx_edi_cfdi_payment_document_sent(
            invoices, cfdi_filename, cfdi_str,
        )
        self.origin_payment_id._assign_receipt_numbers()
        return document

    def _l10n_mx_edi_cfdi_invoice_get_reconciled_payments_values(self):
        """Keep component amounts for calculation but expose one fiscal payment.

        Odoo needs to see every accounting payment to compute the correct
        partiality and previous/remaining balances. Outside the internal
        calculation context, all components are represented by their primary
        payment so invoice update/cancellation tracking remains attached to a
        single REP.
        """
        moves = self.with_context(multiple_payment_include_components=True)
        results = super(AccountMove, moves)._l10n_mx_edi_cfdi_invoice_get_reconciled_payments_values()
        if self.env.context.get("multiple_payment_keep_components"):
            return results
        for pay_results_list in results.values():
            for pay_results in pay_results_list:
                payment_move = pay_results["payment"]
                payment = payment_move.origin_payment_id
                primary = payment.distributed_payment_primary_id
                if primary:
                    pay_results["multiple_payment_component"] = payment_move
                    pay_results["payment"] = primary.move_id
        return results

    def _l10n_mx_edi_cfdi_payment_get_reconciled_invoice_values(self):
        """Collapse linked accounting payments into one multi-node REP."""
        grouped_payments = self.origin_payment_id.filtered(
            "distributed_payment_primary_id"
        )
        grouped_moves = grouped_payments.move_id
        standalone_moves = self - grouped_moves
        results = super(AccountMove, standalone_moves)._l10n_mx_edi_cfdi_payment_get_reconciled_invoice_values()
        for payment_move, payment_result in results.items():
            payment = payment_move.origin_payment_id
            if not payment.multiple_invoice_distribution:
                continue
            for invoice_result in payment_result["invoice_results"]:
                distribution = payment.invoice_distribution_ids.filtered(
                    lambda line: line.invoice_id == invoice_result["invoice"]
                )[:1]
                if distribution and distribution.shortfall_amount:
                    # The separate expense entry settles accounting only.
                    # REP balances must describe the money actually received.
                    invoice_result["amount_residual_before"] = distribution.previous_residual
                    invoice_result["amount_residual_after"] = distribution.remaining_residual

        primaries = grouped_payments.mapped("distributed_payment_primary_id")
        for primary in primaries:
            components = primary._get_distributed_payment_components()
            component_moves = components.move_id.with_context(
                multiple_payment_include_components=True,
                multiple_payment_keep_components=True,
            )
            component_results = super(
                AccountMove,
                component_moves,
            )._l10n_mx_edi_cfdi_payment_get_reconciled_invoice_values()

            # An open customer credit intentionally holds the REP until every
            # component is fully reconciled, matching Odoo's standard rule.
            if any(move not in component_results for move in component_moves):
                continue

            ordered_results = []
            invoices = self.env["account.move"]
            invoice_results = []
            grouped_results_by_invoice = {}
            for component in components:
                component_result = component_results[component.move_id]
                distributions = component.invoice_distribution_ids
                for invoice_result in component_result["invoice_results"]:
                    distribution = distributions.filtered(
                        lambda line: line.invoice_id == invoice_result["invoice"]
                    )[:1]
                    if distribution:
                        # Core orders same-day reconciliations by accounting
                        # internals, which can invert the balances of two Pago
                        # nodes. The wizard stored the exact sequential balance
                        # of every component, so it is the authoritative value
                        # for ImpSaldoAnt and ImpSaldoInsoluto.
                        invoice_result["amount_residual_before"] = (
                            distribution.previous_residual
                        )
                        invoice_result["amount_residual_after"] = (
                            distribution.remaining_residual
                        )
                ordered_results.append((component.move_id, component_result))
                invoices |= component_result["invoices"]
                invoice_results += component_result["invoice_results"]
                for invoice_result in component_result["invoice_results"]:
                    grouped_results_by_invoice.setdefault(
                        invoice_result["invoice"],
                        [],
                    ).append(invoice_result)

            # Core normally emits one REP at a time and therefore assigns the
            # latest partiality number to that payment. When two or three Pago
            # nodes are emitted together, assign their consecutive numbers.
            for grouped_invoice_results in grouped_results_by_invoice.values():
                total_payment_count = max(
                    values["number_of_payments"]
                    for values in grouped_invoice_results
                )
                first_number = total_payment_count - len(grouped_invoice_results) + 1
                for offset, values in enumerate(grouped_invoice_results):
                    values["number_of_payments"] = first_number + offset
            results[primary.move_id] = {
                "invoices": invoices,
                "invoice_results": invoice_results,
                "multiple_payment_components": ordered_results,
            }
        return results

    def _l10n_mx_edi_add_payment_cfdi_values(self, cfdi_values, pay_results):
        """Use the explicitly selected payer account as the REP ordering account.

        The fiscal receiver deliberately remains the receiver prepared by
        ``l10n_mx_edi`` from the related invoices.  A third-party payer is an
        operational attribute and must not move the customer's receivable or
        replace the receiver of those invoices.
        """
        self.ensure_one()
        component_results = pay_results.get("multiple_payment_components")
        if not component_results:
            result = super()._l10n_mx_edi_add_payment_cfdi_values(
                cfdi_values,
                pay_results,
            )
            self._multiple_invoice_apply_payer_bank_cfdi_values(cfdi_values)
            if not cfdi_values.get("errors"):
                cfdi_values["payment_nodes"] = [
                    {key: cfdi_values.get(key) for key in _PAYMENT_NODE_KEYS}
                ]
            return result

        allowed_invoices = pay_results["invoices"]
        payment_nodes = []
        prepared_values = []
        for component_move, raw_component_result in component_results:
            filtered_invoice_results = [
                values
                for values in raw_component_result["invoice_results"]
                if values["invoice"] in allowed_invoices
            ]
            if not filtered_invoice_results:
                continue
            component_result = {
                "invoices": raw_component_result["invoices"] & allowed_invoices,
                "invoice_results": filtered_invoice_results,
            }
            # The PAC helper seeds company/certificate values before calling
            # this method. Each standard node needs the same base context.
            node_values = dict(cfdi_values)
            super(AccountMove, component_move)._l10n_mx_edi_add_payment_cfdi_values(
                node_values,
                component_result,
            )
            component_move._multiple_invoice_apply_payer_bank_cfdi_values(node_values)
            if node_values.get("errors"):
                cfdi_values["errors"] = node_values["errors"]
                return
            prepared_values.append(node_values)
            payment_nodes.append({
                key: node_values.get(key)
                for key in _PAYMENT_NODE_KEYS
            })

        if not prepared_values:
            cfdi_values["errors"] = [
                "No PPD invoice allocation was found for the grouped payment."
            ]
            return

        cfdi_values.update(prepared_values[0])
        company_currency = self.company_id.currency_id
        cfdi_values["monto_total_pagos"] = company_currency.round(sum(
            values.get("monto_total_pagos") or 0.0
            for values in prepared_values
        ))
        for key in _PAYMENT_TOTAL_KEYS:
            amounts = [values.get(key) for values in prepared_values]
            cfdi_values[key] = (
                company_currency.round(sum(filter(None, amounts)))
                if any(amount is not None for amount in amounts)
                else None
            )
        cfdi_values["payment_nodes"] = payment_nodes

    def _multiple_invoice_apply_payer_bank_cfdi_values(self, cfdi_values):
        """Apply the selected payer bank to one prepared Pago node."""
        self.ensure_one()
        payment = self.origin_payment_id
        if (
            not payment
            or payment.distributed_document_kind == "remission"
            or cfdi_values.get("errors")
        ):
            return
        payer = payment.distributed_payer_id.commercial_partner_id
        customer = payment.partner_id.commercial_partner_id
        if not payment.distributed_payer_bank_id:
            if payer and payer != customer:
                # Core l10n_mx_edi falls back to the invoice customer's first
                # bank account. That account cannot represent an unrelated
                # third-party payer, so omit the optional ordering data.
                cfdi_values.update({
                    "rfc_emisor_cta_ord": False,
                    "nom_banco_ord_ext": False,
                    "cta_ordenante": False,
                })
            return
        payer_bank_account = payment.distributed_payer_bank_id

        payment_method_code = self.l10n_mx_edi_payment_method_id.code
        emitter_account_allowed = payment_method_code in (
            "02", "03", "04", "05", "06", "28", "29", "99",
        )
        emitter_bank_allowed = payment_method_code in (
            "02", "03", "04", "28", "29", "99",
        )
        payer_bank = payer_bank_account.bank_id
        if payer_bank.country and payer_bank.country.code != "MX":
            payer_bank_vat = "XEXX010101000"
        else:
            payer_bank_vat = payer_bank.l10n_mx_edi_vat

        ordering_account = re.sub(
            r"\s+",
            "",
            payer_bank_account.acc_number or "",
        ) or None
        cfdi_values.update({
            "rfc_emisor_cta_ord": emitter_account_allowed and payer_bank_vat,
            "nom_banco_ord_ext": emitter_bank_allowed and payer_bank.name,
            "cta_ordenante": emitter_account_allowed and ordering_account,
        })
