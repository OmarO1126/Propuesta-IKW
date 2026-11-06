from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    is_remission = fields.Boolean(
        related="sale_id.is_remission",
        string="Es una remisión",
        store=True,
        readonly=True,
    )
    remission_issuer_company_id = fields.Many2one(
        related="sale_id.remission_issuer_company_id",
        string="Razón social de la remisión",
        store=True,
        readonly=True,
    )
    remission_issuer_partner_id = fields.Many2one(
        related="sale_id.remission_issuer_partner_id",
        string="Contacto emisor de remisiones",
        store=True,
        readonly=True,
    )

    def _get_remission_report_lines(self):
        """One priced row per collected sale line on this OUT."""
        self.ensure_one()
        lines_by_key = {}
        currency = self.sale_id.currency_id or self.company_id.currency_id
        for move in self.move_ids.filtered(lambda record: record.state != "cancel"):
            sale_line = move.sale_line_id
            if sale_line and (sale_line.display_type or sale_line.is_downpayment):
                continue
            quantity = move.quantity
            if not quantity:
                continue
            if sale_line and move.product_uom != sale_line.product_uom_id:
                quantity = move.product_uom._compute_quantity(
                    quantity, sale_line.product_uom_id
                )
            key = ("sale", sale_line.id) if sale_line else ("move", move.id)
            if key not in lines_by_key:
                description = sale_line.name if sale_line else move.description_picking
                description = description or ""
                for product_name in (move.product_id.display_name, move.product_id.name):
                    if description == product_name:
                        description = ""
                        break
                    if description.startswith(product_name + "\n"):
                        description = description[len(product_name):].strip()
                        break
                lines_by_key[key] = {
                    "product": move.product_id,
                    "code": move.product_id.default_code or "",
                    "description": description,
                    "quantity": 0.0,
                    "uom": sale_line.product_uom_id if sale_line else move.product_uom,
                    "sat_code": move.product_id.unspsc_code_id.code or "",
                    "unit_price": sale_line.price_unit if sale_line else False,
                    "discount": sale_line.discount if sale_line else 0.0,
                    "priced": bool(sale_line),
                }
            lines_by_key[key]["quantity"] += quantity

        lines = list(lines_by_key.values())
        for line in lines:
            line["sat_uom_code"] = line["uom"].unspsc_code_id.code or ""
            line["subtotal"] = currency.round(
                line["quantity"] * line["unit_price"]
                * (1 - line["discount"] / 100)
            ) if line["priced"] else False
        return lines
