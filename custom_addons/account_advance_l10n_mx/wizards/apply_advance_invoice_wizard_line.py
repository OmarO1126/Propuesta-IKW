from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class ApplyAdvanceInvoiceWizardLine(models.TransientModel):
    _name = 'apply.advance.invoice.wizard.line'
    _description = 'Línea de Asistente de Anticipos'

    wizard_id = fields.Many2one('apply.advance.invoice.wizard', string="Wizard")
    
    advance_id = fields.Many2one(
        'account.move',
        string="Factura de anticipo",
        domain="[('is_advance_invoice', '=', True), ('state', '=', 'posted'), ('partner_id', '=', parent.partner_id), ('remaining_amount', '>', 0)]",
        required=True
    )
    remaining_amount = fields.Monetary(string="Saldo disponible", related='advance_id.remaining_amount', readonly=True)
    amount = fields.Monetary(string="Monto a aplicar", required=True)
    tax_id = fields.Many2one('account.tax', string="Impuesto", domain="[('type_tax_use', '=', 'sale'), ('price_include', '=', False)]")
    currency_id = fields.Many2one(related='wizard_id.currency_id', store=True)

    @api.onchange('advance_id')
    def _onchange_advance_id(self):
        if self.advance_id:
            # Validación de duplicados (excluyendo el registro actual correctamente)
            if self.wizard_id and self.wizard_id.line_ids:
                matching_lines = self.wizard_id.line_ids.filtered(
                    lambda l: l != self and l._origin != self._origin and l.advance_id == self.advance_id
                )
                if matching_lines:
                    self.advance_id = False
                    self.amount = 0.0
                    return {
                        'warning': {
                            'title': _("Factura duplicada"),
                            'message': _("Ya has agregado esta factura de anticipo en otra línea.")
                        }
                    }

            # Sugerimos el monto respetando el saldo de la factura principal
            remaining_invoice = self.wizard_id.invoice_amount_residual
            current_lines_sum = sum(self.wizard_id.line_ids.filtered(lambda l: l != self and l._origin != self._origin).mapped('amount'))
            available_for_this = max(0.0, remaining_invoice - current_lines_sum)
            
            self.amount = min(self.advance_id.remaining_amount, available_for_this)