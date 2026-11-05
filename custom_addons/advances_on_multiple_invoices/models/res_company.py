from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    remission_issuer_company_id = fields.Many2one(
        comodel_name="res.company",
        string="Empresa emisora anterior de remisiones",
        help="Campo anterior conservado para consultar remisiones históricas.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    remission_issuer_company_id = fields.Many2one(
        related="company_id.remission_issuer_company_id",
        readonly=False,
    )
    remission_issuer_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contacto emisor de remisiones",
        default=lambda self: self.env.ref(
            "advances_on_multiple_invoices.partner_remission_issuer",
            raise_if_not_found=False,
        ),
        readonly=True,
    )
