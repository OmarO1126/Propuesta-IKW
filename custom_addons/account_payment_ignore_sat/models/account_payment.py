from odoo import models, fields

class AccountPayment(models.Model):
    _inherit = 'account.payment'

    x_ignore_sat_payment = fields.Boolean(
        string='Ignorar Timbrado SAT',
        default=False,
        help='Si está marcado, este pago será ignorado y no se generará ni timbrará su Complemento de Pago en el SAT.'
    )

    def _l10n_mx_edi_is_required_for_payment(self):
        """ Evita que la localización considere que requiere CFDI si está marcado. """
        res = super()._l10n_mx_edi_is_required_for_payment()
        if self.x_ignore_sat_payment:
            return False
        return res