from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def action_open_source_sale_order(self):
        self.ensure_one()

        if not self.sale_id:
            raise UserError(
                _("Esta transferencia no tiene un pedido de venta relacionado.")
            )

        return {
            "type": "ir.actions.act_window",
            "name": _("Solicitud origen"),
            "res_model": "sale.order",
            "res_id": self.sale_id.id,
            "view_mode": "form",
            "target": "current",
        }