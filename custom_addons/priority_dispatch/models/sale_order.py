from odoo import models, fields

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_high_priority = fields.Boolean(
        string='Despacho Prioritario',
        default=False,
        help='Si está activo, permite surtir la orden sin importar el orden de llegada de otras ventas.'
    )