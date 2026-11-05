from odoo import models, fields

class AdvanceInvoiceLine(models.Model):
    _name = 'advance.invoice.line'
    _description = 'Línea de aplicación de anticipo'

    invoice_id = fields.Many2one('account.move', string="Factura de venta", required=True, ondelete='cascade')
    advance_id = fields.Many2one('account.move', string="Factura de anticipo", required=True, ondelete='cascade')
    amount = fields.Monetary(string="Monto Aplicado", required=True)
    tax_id = fields.Many2one('account.tax', string="Impuesto aplicado")
    total_with_tax = fields.Monetary(string="Total con impuesto", required=True)
    currency_id = fields.Many2one(related='invoice_id.currency_id', store=True)