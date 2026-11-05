from odoo import models, fields, api
from odoo.exceptions import UserError

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    is_high_priority = fields.Boolean(
        related='sale_id.is_high_priority',
        store=True,
        string='Despacho Prioritario',
        readonly=True
    )

    def button_validate(self):
        for record in self:
            if record.sale_id and not record.is_high_priority:

                previo = self.env['stock.picking'].search([
                    ('picking_type_id', '=', record.picking_type_id.id),
                    ('state', 'not in', ['done', 'cancel']),
                    ('sale_id.id', '<', record.sale_id.id),
                    ('sale_id.is_high_priority', '=', False)
                ], limit=1)

                if previo:
                    raise UserError(
                        f"¡Alto! No puedes surtir esta orden. La orden anterior {previo.origin} ({previo.name}) aún está pendiente de surtir.")

        return super(StockPicking, self).button_validate()