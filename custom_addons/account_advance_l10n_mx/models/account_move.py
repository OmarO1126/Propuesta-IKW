from odoo import models, fields, api

class AccountMove(models.Model):
    _inherit = 'account.move'

    # Campo booleano nativo directo
    is_advance_invoice = fields.Boolean(
        string="Es un Anticipo", 
        default=False,
        copy=False,
        help="Indica si esta factura corresponde a un anticipo recibido."
    )
    has_available_advances = fields.Boolean(
        string="Tiene Anticipos Disponibles",
        compute='_compute_has_available_advances'
    )
    # Factura de anticipo relacionada en la venta final
    advance_invoice_id = fields.Many2one(
        'account.move',
        string='Factura de anticipo relacionada',
        domain="[('is_advance_invoice','=',True)]",
        help="Factura de anticipo vinculada a esta factura de venta."
    )

    # Líneas de control y trazabilidad
    advance_line_ids = fields.One2many('advance.invoice.line', 'invoice_id', string="Anticipos Aplicados")
    advance_usage_line_ids = fields.One2many('advance.invoice.line', 'advance_id', string="Usos del anticipo")
    
    # Saldo disponible calculado en tiempo real para los anticipos
    remaining_amount = fields.Monetary(
        string="Saldo Disponible", 
        compute="_compute_remaining_amount", 
        store=True,
        help="Monto restante disponible de este anticipo para aplicarse en otras facturas."
    )

    
    @api.depends('partner_id', 'state', 'move_type')
    def _compute_has_available_advances(self):
        for move in self:
            # La factura actual (la de venta normal) solo necesita estar confirmada/publicada
            if (move.move_type == 'out_invoice' 
                and not move.is_advance_invoice 
                and move.partner_id 
                and move.state == 'posted'):
                
                # Buscamos anticipos del cliente que cumplan estrictamente con:
                # 1. Ser factura de anticipo (is_advance_invoice = True)
                # 2. Estar confirmadas (state = 'posted')
                # 3. Estar timbradas (l10n_mx_edi_cfdi_uuid no vacío)
                # 4. Estar pagadas (payment_state == 'paid' o tener saldo, aunque el payment_state garantiza que ya fue pagada)
                # 5. Tener saldo remanente mayor a 0
                advances_count = self.env['account.move'].search_count([
                    ('move_type', '=', 'out_invoice'),
                    ('is_advance_invoice', '=', True),
                    ('state', '=', 'posted'),
                    ('l10n_mx_edi_cfdi_uuid', '!=', False),
                    ('payment_state', '=', 'paid'),
                    ('partner_id', '=', move.partner_id.id),
                    ('remaining_amount', '>', 0)
                ])
                move.has_available_advances = advances_count > 0
            else:
                move.has_available_advances = False
                
    @api.depends('is_advance_invoice', 'amount_total', 'advance_usage_line_ids.amount')
    def _compute_remaining_amount(self):
        for move in self:
            if move.is_advance_invoice:
                applied_lines = self.env['advance.invoice.line'].search([
                    ('advance_id', '=', move.id)
                ])
                applied = sum(applied_lines.mapped('total_with_tax'))
                move.remaining_amount = move.amount_total - applied
            else:
                move.remaining_amount = 0.0

    def action_open_apply_advance_wizard(self):
        """Abre el wizard unificado para aplicar y vincular el anticipo"""
        self.ensure_one()
        return {
            'name': 'Aplicar y Vincular Anticipo',
            'type': 'ir.actions.act_window',
            'res_model': 'apply.advance.invoice.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_id': self.id,
            },
        }