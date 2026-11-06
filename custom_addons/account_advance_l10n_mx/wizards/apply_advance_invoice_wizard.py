from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

class ApplyAdvanceInvoiceWizard(models.TransientModel):
    _name = 'apply.advance.invoice.wizard'
    _description = 'Asistente para Aplicar Múltiples Anticipos'

    invoice_id = fields.Many2one('account.move', string="Factura de venta", required=True)
    partner_id = fields.Many2one('res.partner', string="Cliente", related='invoice_id.partner_id', store=True)
    currency_id = fields.Many2one(related='invoice_id.currency_id', store=True)
    
    invoice_amount_residual = fields.Monetary(
        string="Monto por cubrir", 
        related='invoice_id.amount_residual', 
        readonly=True
    )
    
    line_ids = fields.One2many('apply.advance.invoice.wizard.line', 'wizard_id', string="Anticipos a aplicar")

    def action_apply_and_link(self):
        self.ensure_one()
        
        if not self.line_ids:
            raise ValidationError(_("Debe agregar al menos un anticipo en la tabla para aplicar."))

        # 1. Validación de duplicados
        seen_advances = set()
        for line in self.line_ids:
            if not line.advance_id:
                continue
            if line.advance_id in seen_advances:
                raise ValidationError(_("La factura de anticipo '%s' está repetida en las líneas.") % line.advance_id.name)
            seen_advances.add(line.advance_id)

        # 2. Validación de montos
        total_applied = sum(self.line_ids.mapped('amount'))
        if total_applied > self.invoice_amount_residual:
            raise ValidationError(
                _("El monto total a aplicar (%.2f) excede el saldo pendiente por cubrir de la factura de venta (%.2f).") 
                % (total_applied, self.invoice_amount_residual)
            )

        # 3. Recolectar UUIDs de los anticipos para armar el nodo 07
        uuids = []
        for line in self.line_ids:
            if not line.advance_id.l10n_mx_edi_cfdi_uuid:
                raise ValidationError(_("La factura de anticipo %s no cuenta con UUID de timbrado.") % line.advance_id.name)
            
            if line.amount <= 0:
                raise ValidationError(_("El monto a aplicar debe ser mayor a cero."))

            if line.amount > line.remaining_amount:
                raise ValidationError(_("El monto a aplicar excede el saldo disponible del anticipo: %s") % line.advance_id.name)

            uuids.append(line.advance_id.l10n_mx_edi_cfdi_uuid.strip())

        cfdi_origin_value = f"07|{','.join(uuids)}"

        # 4. Escribir el nodo 07 en la factura final antes de timbrar
        self.invoice_id.write({
            'l10n_mx_edi_cfdi_origin': cfdi_origin_value,
        })

        # 5. Intentar timbrar la factura final
        if self.invoice_id.l10n_mx_edi_cfdi_state != 'sent':
            self.invoice_id._l10n_mx_edi_cfdi_invoice_try_send()
            self.invoice_id.invalidate_recordset(['l10n_mx_edi_cfdi_uuid', 'l10n_mx_edi_cfdi_state'])

        final_invoice_uuid = self.invoice_id.l10n_mx_edi_cfdi_uuid and self.invoice_id.l10n_mx_edi_cfdi_uuid.strip() or False
        if not final_invoice_uuid:
            error_msg = _("El PAC rechazó el timbrado de la factura final.")
            if hasattr(self.invoice_id, 'l10n_mx_edi_invoice_document_ids'):
                for doc in self.invoice_id.l10n_mx_edi_invoice_document_ids:
                    if doc.state in ('invoice_sent_failed', 'error') and doc.message:
                        error_msg = f"{error_msg}\n\nDetalle: {doc.message}"
                        break
            raise UserError(error_msg)

        # Generar PDF y adjuntos de la factura final usando el motor nativo de account.move.send
        self.env['account.move.send']._generate_and_send_invoices(
            self.invoice_id, 
            sending_methods=set()
        )

        credit_note_origin = f"07|{final_invoice_uuid}"

        # Buscar la forma de pago '03' (Transferencia)
        payment_method_03 = self.env['l10n_mx_edi.payment.method'].search([('code', '=', '03')], limit=1)
        payment_method_val = payment_method_03.id if payment_method_03 else False

        advance_lines_to_create = []
        advance_product = self.env['product.product'].browse(1802)
        product_id_val = advance_product.id if advance_product.exists() else False

        # Buscar el impuesto de venta del 16% de manera predeterminada
        tax_16 = self.env['account.tax'].search([
            ('type_tax_use', '=', 'sale'),
            ('amount', '=', 16.0),
            ('company_id', '=', self.env.company.id)
        ], limit=1)

        # 6. Crear, timbrar, generar PDF y conciliar cada Nota de Crédito
        for line in self.line_ids:
            tax_factor = 1.16  # Equivalente a 1 + (16.0 / 100.0)
            amount_untaxed = line.amount / tax_factor

            credit_note_vals = {
                'move_type': 'out_refund',
                'partner_id': self.partner_id.id,
                'invoice_origin': self.invoice_id.name,
                'l10n_mx_edi_cfdi_origin': credit_note_origin,
                'invoice_line_ids': [(0, 0, {
                    'product_id': product_id_val,
                    'name': _("Aplicación de anticipo: %s") % line.advance_id.name,
                    'quantity': 1,
                    'price_unit': amount_untaxed,
                    'account_id': self.invoice_id.line_ids.filtered(lambda l: l.credit > 0)[:1].account_id.id,
                    'tax_ids': [(6, 0, [tax_16.id])] if tax_16 else False,
                })],
            }
            
            if payment_method_val:
                credit_note_vals['l10n_mx_edi_payment_method_id'] = payment_method_val

            credit_note = self.env['account.move'].create(credit_note_vals)

            if hasattr(credit_note, 'l10n_mx_edi_payment_policy'):
                credit_note.write({'l10n_mx_edi_payment_policy': 'PUE'})

            credit_note.action_post()

            # Timbrar la Nota de Crédito
            if credit_note.l10n_mx_edi_cfdi_state != 'sent':
                credit_note._l10n_mx_edi_cfdi_invoice_try_send()

            if not credit_note.l10n_mx_edi_cfdi_uuid:
                nc_error = _("El PAC rechazó el timbrado de la nota de crédito para el anticipo %s.") % line.advance_id.name
                if hasattr(credit_note, 'l10n_mx_edi_invoice_document_ids'):
                    for doc in credit_note.l10n_mx_edi_invoice_document_ids:
                        if doc.state in ('invoice_sent_failed', 'error') and doc.message:
                            nc_error = f"{nc_error}\n\nDetalle: {doc.message}"
                            break
                raise UserError(nc_error)

            # Generar PDF y adjuntos de la Nota de Crédito usando el motor nativo
            self.env['account.move.send']._generate_and_send_invoices(
                credit_note, 
                sending_methods=set()
            )

            # Conciliar contra la factura final
            (credit_note + self.invoice_id).line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').reconcile()

            advance_lines_to_create.append((0, 0, {
                'invoice_id': self.invoice_id.id,
                'advance_id': line.advance_id.id,
                'amount': amount_untaxed,
                'tax_id': tax_16.id if tax_16 else False,
                'total_with_tax': line.amount,
            }))

        self.invoice_id.write({
            'advance_line_ids': advance_lines_to_create,
        })

        return {'type': 'ir.actions.act_window_close'}