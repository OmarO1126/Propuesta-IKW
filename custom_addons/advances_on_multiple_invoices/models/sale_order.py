from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.mail import html2plaintext


class SaleOrder(models.Model):
    _inherit = "sale.order"

    is_remission = fields.Boolean(
        string="Es una remisión",
        copy=False,
        help=(
            "Identifica la operación como remisión interna desde la cotización. "
            "La marca se transmite a la entrega y a cualquier documento "
            "contable interno generado desde el pedido."
        ),
    )
    remission_issuer_company_id = fields.Many2one(
        comodel_name="res.company",
        string="Empresa emisora anterior de la remisión",
        copy=False,
        help="Campo conservado para consultar remisiones anteriores.",
    )
    remission_issuer_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contacto emisor de remisiones",
        copy=False,
        domain="[('is_remission_issuer_contact', '=', True)]",
        help=(
            "Contacto único que identifica al emisor en el formato de remisión. "
            "Abre el contacto para modificar su razón social, RFC y domicilio."
        ),
    )
    has_completed_remission_out = fields.Boolean(
        compute="_compute_has_completed_remission_out",
        string="Tiene salida de remisión completada",
    )

    @api.depends("is_remission", "picking_ids.state", "picking_ids.picking_type_code")
    def _compute_has_completed_remission_out(self):
        for order in self:
            order.has_completed_remission_out = bool(
                order.is_remission and order.picking_ids.filtered(
                    lambda picking: picking.picking_type_code == "outgoing"
                    and picking.state == "done"
                )
            )

    def action_print_remission(self):
        self.ensure_one()
        outgoing_pickings = self.picking_ids.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
            and picking.state != "cancel"
        ).sorted(lambda picking: (picking.date_done or picking.scheduled_date, picking.id))
        if not self.is_remission:
            raise UserError(_("Esta orden no está marcada como remisión."))
        if outgoing_pickings:
            collected_pickings = outgoing_pickings.filtered(
                lambda picking: any(
                    move.state != "cancel" and move.quantity > 0
                    for move in picking.move_ids
                )
            )
            return self.env.ref(
                "advances_on_multiple_invoices.action_report_remission_delivery"
            ).report_action(collected_pickings or outgoing_pickings[:1])
        return self.env.ref(
            "advances_on_multiple_invoices.action_report_remission_delivery_empty"
        ).report_action(self)

    def _get_remission_transport_text(self):
        """Read the customer's existing Transporte field without requiring Studio."""
        self.ensure_one()
        preferred_names = (
            "x_studio_transporte",
            "x_transporte",
        )
        matching_names = tuple(
            name for name, field in self._fields.items()
            if name.startswith("x_")
            and (field.string or "").strip().casefold() == "transporte"
        )
        for name in dict.fromkeys((*preferred_names, *matching_names)):
            field = self._fields.get(name)
            if not field or field.type not in ("char", "text", "selection", "many2one"):
                continue
            value = self[name]
            if value:
                return value.display_name if field.type == "many2one" else str(value)
        return ""

    def _get_remission_observations_text(self):
        """Use the order's Observaciones field, not its terms/conditions note."""
        self.ensure_one()
        preferred_names = (
            "x_studio_observaciones",
            "x_observaciones",
        )
        matching_names = tuple(
            name for name, field in self._fields.items()
            if (field.string or "").strip().rstrip(":").casefold() == "observaciones"
        )
        for name in dict.fromkeys((*preferred_names, *matching_names)):
            field = self._fields.get(name)
            if not field or field.type not in ("char", "text", "html", "selection", "many2one"):
                continue
            value = self[name]
            if value:
                if field.type == "many2one":
                    return value.display_name
                if field.type == "html":
                    return html2plaintext(value).strip()
                return str(value).strip()
        return ""

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            if (
                values.get("is_remission")
                and not values.get("remission_issuer_partner_id")
            ):
                issuer = self.env.ref(
                    "advances_on_multiple_invoices.partner_remission_issuer",
                    raise_if_not_found=False,
                )
                values["remission_issuer_partner_id"] = issuer.id if issuer else False
        return super().create(values_list)

    @api.onchange("is_remission")
    def _onchange_remission_issuer_contact(self):
        for order in self:
            if not order.is_remission:
                order.remission_issuer_partner_id = False
            elif not order.remission_issuer_partner_id:
                order.remission_issuer_partner_id = self.env.ref(
                    "advances_on_multiple_invoices.partner_remission_issuer",
                    raise_if_not_found=False,
                )

    def action_confirm(self):
        dedicated_issuer = self.env.ref(
            "advances_on_multiple_invoices.partner_remission_issuer",
            raise_if_not_found=False,
        )
        for order in self.filtered("is_remission"):
            issuer = order.remission_issuer_partner_id or dedicated_issuer
            if not issuer or issuer != dedicated_issuer:
                raise ValidationError(_(
                    "La remisión debe usar el contacto emisor único configurado para este módulo."
                ))
            if not order.remission_issuer_partner_id:
                order.remission_issuer_partner_id = issuer
            if (
                issuer.name == "Emisor de remisiones (editar datos)"
                or not issuer.street
            ):
                raise ValidationError(_(
                    "Edita el nombre y domicilio del contacto emisor de remisiones "
                    "antes de confirmar esta cotización."
                ))
        return super().action_confirm()

    def action_open_remission_issuer_contact(self):
        self.ensure_one()
        issuer = self.remission_issuer_partner_id or self.env.ref(
            "advances_on_multiple_invoices.partner_remission_issuer"
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Contacto emisor de remisiones"),
            "res_model": "res.partner",
            "res_id": issuer.id,
            "view_mode": "form",
            "target": "current",
        }

    def _prepare_invoice(self):
        values = super()._prepare_invoice()
        values.update({
            "is_remission": self.is_remission,
            "remission_issuer_company_id": self.remission_issuer_company_id.id,
            "remission_issuer_partner_id": self.remission_issuer_partner_id.id,
        })
        if self.is_remission:
            journal = self.env["account.journal"].search([
                ("name", "=", "Remisiones"),
                ("type", "=", "sale"),
                ("company_id", "=", self.company_id.id),
            ], order="sequence, id", limit=1)
            if not journal:
                raise UserError(_(
                    "No se encontró un diario de ventas llamado Remisiones para "
                    "la empresa %(company)s. Configúralo antes de crear la factura; "
                    "no se seleccionará otro diario automáticamente."
                ) % {"company": self.company_id.display_name})
            values["journal_id"] = journal.id
        return values

    def _get_invoice_grouping_keys(self):
        return super()._get_invoice_grouping_keys() + [
            "is_remission",
            "remission_issuer_company_id",
            "remission_issuer_partner_id",
        ]
