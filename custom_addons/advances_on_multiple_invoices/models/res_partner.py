from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_remission_issuer_contact = fields.Boolean(
        string="Emisor de remisiones",
        copy=False,
        readonly=True,
        help="Identifica el contacto único cuyos datos se imprimen en las remisiones.",
    )
